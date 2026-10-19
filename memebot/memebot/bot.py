"""Orquestador: fuentes -> watchlist -> evaluacion -> compra -> seguimiento -> venta."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import aiohttp

from . import risk
from .config import Config
from .exits import ExitAction, decide_exit
from .models import WSOL_MINT, Candidate, Position, TokenSnapshot
from .notifier import Notifier
from .safety import RugCheck
from .scoring import Verdict, evaluate
from .sources.dexscreener import DexScreener, poll_dexscreener
from .sources.gmgn import poll_gmgn
from .sources.pumpportal import stream_pumpportal
from .stages import STAGE_LABELS, classify, progress_from_mcap_sol, progress_from_vsol
from .state import State
from .trader import LiveTrader, PaperTrader

log = logging.getLogger("memebot")

MAX_WATCHLIST = 3000
DUST_FRACTION = 0.01  # si queda menos del 1% de la posicion, se considera cerrada
MAX_SELL_FAILURES = 5
MAX_RADAR = 300
EQUITY_EVERY_SECONDS = 30


class Bot:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self.mode = "live" if cfg.live else "paper"
        self.state = State(cfg.data_dir, self.mode)
        self.dex = DexScreener(session)
        self.rugcheck = RugCheck(session, cfg.filters)
        self.notifier = Notifier(session, cfg.telegram_token, cfg.telegram_chat_id)
        if cfg.live:
            self.trader: LiveTrader | PaperTrader = LiveTrader(cfg, session)
        else:
            self.trader = PaperTrader(cfg)
            if self.state.paper_balance is not None:
                self.trader.balance_sol = self.state.paper_balance
        self.queue: asyncio.Queue[Candidate] = asyncio.Queue()
        self.watchlist: dict[str, Candidate] = {}
        self.rejected: set[str] = set()
        self.sell_failures: dict[str, int] = {}
        self.stats = {"seen": 0, "bought": 0, "rug_rejected": 0}
        self.started_at = time.time()
        self.sol_usd = 0.0
        self.last_wallet_sol = 0.0
        # estado de compras para el panel: "" = puede comprar, si no el motivo de la pausa
        self.buy_block = ""
        self.last_eval_at = 0.0
        # wallet preparada en .env (en simulacion no se usa, solo se muestra en el panel)
        self.prepared_wallet = ""
        if cfg.private_key:
            try:
                from .solana import load_keypair

                self.prepared_wallet = str(load_keypair(cfg.private_key).pubkey())
            except Exception:  # noqa: BLE001
                log.warning("SOLANA_PRIVATE_KEY en .env no es valida")
        # ultima evaluacion de cada token vigilado (para el "radar" del panel)
        self.radar: dict[str, dict] = {}
        self.equity: deque[tuple[float, float]] = deque(
            self.state.load_equity(time.time() - 30 * 86400), maxlen=100_000
        )
        self._selling: set[str] = set()
        # columnas tipo GMGN: creaciones y migraciones recientes de pump.fun
        self.recent_new: deque[dict] = deque(maxlen=60)
        self.recent_migrated: deque[dict] = deque(maxlen=60)
        self.migrated_mints: set[str] = set()

    # ------------------------------------------------------------------ run

    async def run(self) -> None:
        wallet = await self.trader.wallet_sol()
        self.last_wallet_sol = wallet
        who = f" wallet {self.trader.pubkey}" if isinstance(self.trader, LiveTrader) else ""
        await self.notifier.send(
            f"memebot iniciado en modo {self.mode.upper()}{who} | saldo {wallet:.4f} SOL | "
            f"{len(self.state.positions)} posiciones abiertas"
        )
        src = self.cfg.sources
        tasks = [
            asyncio.create_task(self._intake_loop(), name="intake"),
            asyncio.create_task(self._evaluate_loop(), name="evaluate"),
            asyncio.create_task(self._positions_loop(), name="positions"),
            asyncio.create_task(self._status_loop(), name="status"),
            asyncio.create_task(self._sol_price_loop(), name="sol-price"),
            asyncio.create_task(self._equity_loop(), name="equity"),
        ]
        if self.cfg.dashboard.enabled:
            from .dashboard import start_dashboard

            self._dashboard = await start_dashboard(self)
        if src.pumpportal or src.pumpportal_migrations:
            tasks.append(
                asyncio.create_task(
                    stream_pumpportal(
                        self.session,
                        self.queue,
                        new_tokens=src.pumpportal,
                        migrations=src.pumpportal_migrations,
                    ),
                    name="pumpportal",
                )
            )
        if src.dexscreener:
            tasks.append(
                asyncio.create_task(
                    poll_dexscreener(self.dex, self.queue, src.dexscreener_poll_seconds),
                    name="dexscreener",
                )
            )
        if src.gmgn:
            tasks.append(
                asyncio.create_task(
                    poll_gmgn(self.session, self.queue, src.gmgn_poll_seconds), name="gmgn"
                )
            )
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self._save()

    def _save(self) -> None:
        if isinstance(self.trader, PaperTrader):
            self.state.paper_balance = self.trader.balance_sol
        self.state.save()

    # --------------------------------------------------------------- intake

    async def _intake_loop(self) -> None:
        while True:
            cand = await self.queue.get()
            mint = cand.mint
            self._track_stage_event(cand)
            if (
                mint in self.watchlist
                or mint in self.rejected
                or mint in self.state.positions
                or (self.cfg.risk.never_rebuy_same_token and mint in self.state.traded_mints)
            ):
                continue
            self.stats["seen"] += 1
            if len(self.watchlist) >= MAX_WATCHLIST:
                oldest = min(self.watchlist.values(), key=lambda c: c.first_seen)
                self.watchlist.pop(oldest.mint, None)
            self.watchlist[mint] = cand
            log.debug("Nuevo candidato %s %s (%s)", cand.symbol, mint, cand.source)

    def _update_buy_block(self) -> None:
        """Revisa los limites globales (no los de un token puntual) para mostrar en el panel
        si el bot esta en pausa y por que."""
        ok, why = risk.can_open(
            "__probe__", self.state, self.last_wallet_sol, self.cfg.risk, live=self.cfg.live
        )
        if not self.state.buy_stages:
            ok, why = False, "todas las etapas de compra estan apagadas en Trenches"
        block = "" if ok else why
        if block != self.buy_block:
            if block:
                log.info("Compras en pausa: %s", block)
            else:
                log.info("Compras activas de nuevo")
            self.buy_block = block

    def _track_stage_event(self, cand: Candidate) -> None:
        item = {"mint": cand.mint, "symbol": cand.symbol, "name": cand.name, "t": cand.first_seen}
        if cand.source == "pumpfun-new":
            mcap_sol = float(cand.extra.get("market_cap_sol") or 0)
            vsol = float(cand.extra.get("vsol") or 0)
            item["mcap_sol"] = mcap_sol
            item["progress"] = (
                progress_from_vsol(vsol) if vsol else progress_from_mcap_sol(mcap_sol)
            )
            self.recent_new.append(item)
        elif cand.source == "pumpfun-graduated" and cand.mint not in self.migrated_mints:
            self.migrated_mints.add(cand.mint)
            self.recent_migrated.append(item)
            if cand.mint in self.radar:
                self.radar[cand.mint]["stage"] = "migrated"

    def stage_of(self, snap: TokenSnapshot, source: str) -> tuple[str, float | None]:
        progress = None
        if snap.dex_id == "pumpfun" and self.sol_usd > 0:
            progress = progress_from_mcap_sol(snap.market_cap_usd / self.sol_usd)
        stage = classify(snap.dex_id, source, progress, snap.mint in self.migrated_mints)
        if stage == "migrated":
            progress = 1.0
        return stage, progress

    # ------------------------------------------------------------- evaluate

    async def _evaluate_loop(self) -> None:
        f = self.cfg.filters
        while True:
            await asyncio.sleep(f.recheck_seconds)
            now = time.time()
            self.last_eval_at = now
            self._update_buy_block()
            for mint, cand in list(self.watchlist.items()):
                if now - cand.first_seen > f.watch_minutes * 60:
                    self.watchlist.pop(mint, None)
            if not self.watchlist:
                continue
            try:
                snaps = await self.dex.snapshots(self.watchlist.keys())
            except Exception:  # noqa: BLE001
                log.exception("Error obteniendo datos de DexScreener")
                continue

            ranked = []
            for mint, snap in snaps.items():
                cand = self.watchlist.get(mint)
                if not cand:
                    continue
                ev = evaluate(snap, f, now)
                self._update_radar(cand, snap, ev.score, ev.verdict.value, ev.reasons, now)
                if ev.verdict is Verdict.REJECT:
                    self.watchlist.pop(mint, None)
                    self.rejected.add(mint)
                elif ev.verdict is Verdict.BUY:
                    stage = self.radar[mint]["stage"]
                    if stage in self.state.buy_stages:
                        ranked.append((ev.score, cand, snap, stage))
            ranked.sort(key=lambda r: r[0], reverse=True)
            for score, cand, snap, stage in ranked:
                await self._try_buy(cand, snap, score, stage)

    def _update_radar(self, cand, snap, score, verdict, reasons, now) -> None:
        stage, progress = self.stage_of(snap, cand.source)
        self.radar[cand.mint] = {
            "stage": stage,
            "progress": progress,
            "mint": cand.mint,
            "symbol": snap.symbol or cand.symbol,
            "name": snap.name or cand.name,
            "pair": snap.pair_address,
            "dex": snap.dex_id,
            "source": cand.source,
            "score": score,
            "verdict": verdict,
            "reasons": reasons[:4],
            "price": snap.price_usd,
            "mcap": snap.market_cap_usd,
            "liq": snap.liquidity_usd if snap.liquidity_known else None,
            "vol_m5": snap.volume_m5,
            "buys_m5": snap.buys_m5,
            "sells_m5": snap.sells_m5,
            "chg_m5": snap.change_m5,
            "chg_h1": snap.change_h1,
            "age_min": snap.age_minutes(now),
            "t": now,
        }
        if len(self.radar) > MAX_RADAR:
            by_age = sorted(self.radar.values(), key=lambda r: r["t"])
            for old in by_age[: len(self.radar) - MAX_RADAR]:
                self.radar.pop(old["mint"], None)

    async def _try_buy(
        self, cand: Candidate, snap: TokenSnapshot, score: float, stage: str = ""
    ) -> None:
        wallet = await self.trader.wallet_sol()
        ok, why = risk.can_open(cand.mint, self.state, wallet, self.cfg.risk, live=self.cfg.live)
        if not ok:
            log.debug("No se compra %s: %s", snap.symbol, why)
            return
        safety = await self.rugcheck.check(cand.mint)
        if not safety.ok:
            self.stats["rug_rejected"] += 1
            log.info(
                "RugCheck descarta %s (%s): %s", snap.symbol, cand.mint, "; ".join(safety.reasons)
            )
            if safety.reasons != ["RugCheck no disponible"]:
                self.watchlist.pop(cand.mint, None)
                self.rejected.add(cand.mint)
            return

        size = self.cfg.risk.trade_size_sol
        fill = await self.trader.buy(cand.mint, size, snap)
        self.watchlist.pop(cand.mint, None)
        if not fill.ok:
            self.rejected.add(cand.mint)
            await self.notifier.send(f"Compra FALLIDA {snap.symbol}: {fill.error}")
            return
        pos = Position(
            mint=cand.mint,
            symbol=snap.symbol,
            pair_address=snap.pair_address,
            entry_price_usd=snap.price_usd,
            entry_time=time.time(),
            sol_spent=fill.sol_amount,
            tokens_initial=fill.token_amount,
            tokens_left=fill.token_amount,
            peak_price_usd=snap.price_usd,
            source=cand.source,
            last_price_usd=snap.price_usd,
            signatures=[fill.signature],
            stage=stage,
        )
        self.state.open_position(pos)
        self.state.log_trade(
            "BUY",
            pos,
            fill.sol_amount,
            fill.token_amount,
            snap.price_usd,
            f"score {score}",
            fill.signature,
        )
        self.stats["bought"] += 1
        self._save()
        await self.notifier.send(
            f"🟢 COMPRA {snap.symbol} | {fill.sol_amount:.4f} SOL | score {score} | "
            f"mcap ${snap.market_cap_usd:,.0f} | liq ${snap.liquidity_usd:,.0f} | "
            f"etapa {STAGE_LABELS.get(stage, stage)} | fuente {cand.source}\n"
            f"{snap.url or 'https://dexscreener.com/solana/' + cand.mint}"
        )

    # ------------------------------------------------------------ positions

    async def _positions_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.execution.price_poll_seconds)
            if not self.state.positions:
                continue
            try:
                snaps = await self.dex.snapshots(self.state.positions.keys())
            except Exception:  # noqa: BLE001
                log.exception("Error actualizando precios")
                continue
            for mint, pos in list(self.state.positions.items()):
                snap = snaps.get(mint)
                if snap:
                    liq = snap.liquidity_usd if snap.liquidity_known else None
                    await self.manage_position(pos, snap.price_usd, liq)

    async def manage_position(
        self, pos: Position, price: float, liquidity: float | None, force_reason: str = ""
    ) -> None:
        if pos.mint in self._selling:  # ya hay una venta en curso para esta posicion
            return
        pos.last_price_usd = price
        pos.peak_price_usd = max(pos.peak_price_usd, price)
        if force_reason:
            action: ExitAction | None = ExitAction(1.0, force_reason)
        else:
            action = decide_exit(pos, price, liquidity, time.time(), self.cfg.exits)
        if not action:
            return
        self._selling.add(pos.mint)
        try:
            await self._sell(pos, price, action)
        finally:
            self._selling.discard(pos.mint)

    async def sell_now(self, mint: str) -> tuple[bool, str]:
        """Venta manual desde el panel: vende todo al precio actual."""
        pos = self.state.positions.get(mint)
        if not pos:
            return False, "no hay posicion abierta en ese token"
        snap = await self.dex.snapshot(mint)
        price = snap.price_usd if snap else pos.last_price_usd
        await self.manage_position(pos, price, None, force_reason="venta manual")
        if mint in self.state.positions:
            return False, "la venta no se completo, revisa el log"
        return True, "vendida"

    async def _sell(self, pos: Position, price: float, action: ExitAction) -> None:
        amount = (
            pos.tokens_left
            if action.fraction_of_remaining >= 1
            else (pos.tokens_left * action.fraction_of_remaining)
        )
        fill = await self.trader.sell(pos.mint, amount, price)
        if not fill.ok:
            n = self.sell_failures.get(pos.mint, 0) + 1
            self.sell_failures[pos.mint] = n
            log.warning("Venta fallida %s (%d): %s", pos.symbol, n, fill.error)
            if n == MAX_SELL_FAILURES:
                await self.notifier.send(
                    f"⚠️ No se puede vender {pos.symbol} ({pos.mint}) tras {n} intentos: "
                    f"{fill.error}. Revisalo manualmente."
                )
            return
        self.sell_failures.pop(pos.mint, None)
        pos.tokens_left = max(0.0, pos.tokens_left - fill.token_amount)
        pos.sol_received += fill.sol_amount
        pos.signatures.append(fill.signature)
        if action.tp_index is not None:
            pos.tp_done.append(action.tp_index)
        self.state.log_trade(
            "SELL", pos, fill.sol_amount, fill.token_amount, price, action.reason, fill.signature
        )
        if pos.tokens_left <= pos.tokens_initial * DUST_FRACTION:
            self.state.close_position(pos, action.reason)
            emoji = "✅" if pos.realized_pnl_sol >= 0 else "🔴"
            await self.notifier.send(
                f"{emoji} CERRADA {pos.symbol} | {action.reason} | PnL "
                f"{pos.realized_pnl_sol:+.4f} SOL ({pos.sol_received / pos.sol_spent:.2f}x)"
            )
        else:
            await self.notifier.send(
                f"💰 VENTA PARCIAL {pos.symbol} | {action.reason} | +{fill.sol_amount:.4f} SOL"
            )
        self._save()

    # --------------------------------------------------------------- status

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            wallet = await self.trader.wallet_sol()
            pnl = self.state.realized_pnl_today()
            lines = [
                f"Estado: saldo {wallet:.4f} SOL | PnL hoy {pnl:+.4f} SOL"
                f" | vigilando {len(self.watchlist)} | vistos {self.stats['seen']}"
                f" | compras {self.stats['bought']} | descartados por RugCheck "
                f"{self.stats['rug_rejected']}"
            ]
            for p in self.state.positions.values():
                lines.append(
                    f"  {p.symbol}: {p.multiple(p.last_price_usd):.2f}x "
                    f"(max {p.multiple(p.peak_price_usd):.2f}x)"
                )
            log.info("\n".join(lines))

    # ------------------------------------------------- datos para el panel

    async def _sol_price_loop(self) -> None:
        while True:
            try:
                snap = await self.dex.snapshot(WSOL_MINT)
                if snap and snap.price_usd > 0:
                    self.sol_usd = snap.price_usd
            except Exception:  # noqa: BLE001
                log.debug("No se pudo obtener el precio de SOL", exc_info=True)
            await asyncio.sleep(60)

    def open_value_sol(self) -> float:
        return sum(
            p.unrealized_value_sol(p.last_price_usd or p.entry_price_usd)
            for p in self.state.positions.values()
        )

    async def _equity_loop(self) -> None:
        while True:
            try:
                wallet = await self.trader.wallet_sol()
                self.last_wallet_sol = wallet
                self.equity.append((time.time(), wallet + self.open_value_sol()))
                self.state.append_equity(self.equity[-1])
            except Exception:  # noqa: BLE001
                log.debug("No se pudo registrar el equity", exc_info=True)
            await asyncio.sleep(EQUITY_EVERY_SECONDS)
