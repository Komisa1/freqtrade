"""Orquestador: fuentes -> watchlist -> evaluacion -> compra -> seguimiento -> venta."""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from . import risk
from .config import Config
from .exits import decide_exit
from .models import Candidate, Position, TokenSnapshot
from .notifier import Notifier
from .safety import RugCheck
from .scoring import Verdict, evaluate
from .sources.dexscreener import DexScreener, poll_dexscreener
from .sources.gmgn import poll_gmgn
from .sources.pumpportal import stream_pumpportal
from .state import State
from .trader import LiveTrader, PaperTrader

log = logging.getLogger("memebot")

MAX_WATCHLIST = 3000
DUST_FRACTION = 0.01  # si queda menos del 1% de la posicion, se considera cerrada
MAX_SELL_FAILURES = 5


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

    # ------------------------------------------------------------------ run

    async def run(self) -> None:
        wallet = await self.trader.wallet_sol()
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
        ]
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

    # ------------------------------------------------------------- evaluate

    async def _evaluate_loop(self) -> None:
        f = self.cfg.filters
        while True:
            await asyncio.sleep(f.recheck_seconds)
            now = time.time()
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
                if ev.verdict is Verdict.REJECT:
                    self.watchlist.pop(mint, None)
                    self.rejected.add(mint)
                elif ev.verdict is Verdict.BUY:
                    ranked.append((ev.score, cand, snap))
            ranked.sort(key=lambda r: r[0], reverse=True)
            for score, cand, snap in ranked:
                await self._try_buy(cand, snap, score)

    async def _try_buy(self, cand: Candidate, snap: TokenSnapshot, score: float) -> None:
        wallet = await self.trader.wallet_sol()
        ok, why = risk.can_open(cand.mint, self.state, wallet, self.cfg.risk)
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
            f"fuente {cand.source}\n{snap.url or 'https://dexscreener.com/solana/' + cand.mint}"
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

    async def manage_position(self, pos: Position, price: float, liquidity: float | None) -> None:
        pos.last_price_usd = price
        pos.peak_price_usd = max(pos.peak_price_usd, price)
        action = decide_exit(pos, price, liquidity, time.time(), self.cfg.exits)
        if not action:
            return
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
