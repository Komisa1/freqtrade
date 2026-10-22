"""Orquestador: fuentes -> watchlist -> evaluacion -> compra -> seguimiento -> venta."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import aiohttp

from . import risk, survival
from .config import Config
from .entry import Armed, check_pullback
from .exits import ExitAction, decide_exit
from .models import WSOL_MINT, Candidate, Position, TokenSnapshot
from .notifier import Notifier
from .regime import RegimeTracker
from .safety import RugCheck
from .scoring import Verdict, evaluate
from .sources.dexscreener import DexScreener, poll_dexscreener
from .sources.gmgn import poll_gmgn
from .sources.pumpportal import stream_pumpportal
from .stages import STAGE_LABELS, classify, progress_from_mcap_sol, progress_from_vsol
from .state import State
from .survival import Instinct, Life
from .trader import LiveTrader, PaperTrader

log = logging.getLogger("memebot")

MAX_WATCHLIST = 3000
DUST_FRACTION = 0.01  # si queda menos del 1% de la posicion, se considera cerrada
MAX_SELL_FAILURES = 5
MAX_RADAR = 300
EQUITY_EVERY_SECONDS = 30
MOOD_EVERY = 600


class Bot:
    def __init__(self, cfg: Config, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self.mode = "live" if cfg.live else "paper"
        self.state = State(cfg.data_dir, self.mode)
        self.dex = DexScreener(session)
        self.rugcheck = RugCheck(session, cfg.filters)
        self.regime = RegimeTracker(session)
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
        # tokens que ya calificaron y esperan un retroceso para comprar (modo pullback)
        self.armed: dict[str, Armed] = {}
        # columnas tipo GMGN: creaciones y migraciones recientes de pump.fun
        self.recent_new: deque[dict] = deque(maxlen=60)
        self.recent_migrated: deque[dict] = deque(maxlen=60)
        self.migrated_mints: set[str] = set()
        # instinto de supervivencia: como se siente Marín ahorita (None = desactivado)
        self.instinct: Instinct | None = None
        self._last_mood = ""
        self._mood_said_at = 0.0
        self._dying_since = 0.0  # desde cuando esta debajo de la linea de muerte
        self._liquidating = False
        self._birth_waiting = False
        # en real, compras y ventas una por una: cada fill se mide con el cambio de saldo de
        # la wallet, y dos operaciones a la vez se contarian la una a la otra
        self._trade_lock = asyncio.Lock()

    # ------------------------------------------------------------------ run

    async def run(self) -> None:
        wallet = await self.trader.wallet_sol()
        self.last_wallet_sol = wallet
        await self.ensure_life(wallet)
        await self.check_life()
        who = f" wallet {self.trader.pubkey}" if isinstance(self.trader, LiveTrader) else ""
        life = self.state.life
        alive = ""
        if life and self.instinct:
            alive = (
                f" | 💀 MUERTA (vida {life.number}): no va a comprar"
                if life.dead
                else f" | vida {life.number} {self.instinct.emoji} miedo {self.instinct.fear:.0%}"
            )
        await self.notifier.send(
            f"Marín iniciada en modo {self.mode.upper()}{who} | saldo {wallet:.4f} SOL | "
            f"{len(self.state.positions)} posiciones abiertas{alive}"
        )
        src = self.cfg.sources
        tasks = [
            asyncio.create_task(self._intake_loop(), name="intake"),
            asyncio.create_task(self._evaluate_loop(), name="evaluate"),
            asyncio.create_task(self._positions_loop(), name="positions"),
            asyncio.create_task(self._status_loop(), name="status"),
            asyncio.create_task(self._sol_price_loop(), name="sol-price"),
            asyncio.create_task(self._equity_loop(), name="equity"),
            asyncio.create_task(self._regime_loop(), name="regime"),
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
            self.state.heartbeat(stop=True)

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

    # ------------------------------------------------------------- vida

    def _revive_hint(self) -> str:
        cmd = "python -m memebot revivir" + (" --live" if self.cfg.live else "")
        return f"boton 'Darle otra vida' del panel, o con el bot cerrado: {cmd}"

    def _life_capital(self, wallet: float) -> float:
        # las posiciones de antes de nacer no son de esta vida: el capital es el saldo
        sv = self.cfg.survival
        return sv.life_capital_sol if sv.life_capital_sol > 0 else wallet

    async def _birth(self, number: int, capital: float) -> None:
        sv = self.cfg.survival
        self.state.life = Life(number=number, born_at=time.time(), capital=capital, peak=capital)
        self._dying_since = 0.0
        self._last_mood = ""
        self._save()
        await self.notifier.send(
            f"🐣 Marín nació (vida {number}) con {capital:.4f} SOL. Muere si pierde "
            f"{sv.death_drawdown_pct:.0f}% desde su mejor momento: "
            f"{capital * (1 - sv.death_drawdown_pct / 100):.4f} SOL"
        )

    async def ensure_life(self, wallet: float | None = None) -> None:
        """Hace nacer a Marín si todavia no tiene vida (primera vez o revivida desde la consola).
        Sin saldo para al menos una compra no nace: espera a que deposites."""
        sv = self.cfg.survival
        if not sv.enabled:
            return
        life = self.state.life
        if life is not None and life.born:
            return
        if wallet is None:
            wallet = await self.trader.wallet_sol()
        capital = self._life_capital(wallet)
        if capital < self.cfg.risk.trade_size_sol:
            if not self._birth_waiting:
                log.warning(
                    "Marín espera para nacer: %.4f SOL no alcanza para una compra de %s SOL",
                    capital,
                    self.cfg.risk.trade_size_sol,
                )
                self._birth_waiting = True
            return
        self._birth_waiting = False
        await self._birth(life.number if life is not None else 1, capital)

    async def check_life(self) -> None:
        """Recalcula el miedo; si sigue debajo de la linea de muerte `death_confirm_seconds`
        (para que un precio raido no la mate), muere y vende todo."""
        sv = self.cfg.survival
        if not sv.enabled:
            self.instinct = None
            return
        life = self.state.life
        if life is None or not life.born:
            await self.ensure_life()
            life = self.state.life
            if life is None or not life.born:
                self.instinct = None
                return
        inst = survival.compute_instinct(life, self.state.positions.values(), sv, self.cfg.risk)
        if survival.should_die(inst, sv):
            now = time.time()
            if not self._dying_since:
                self._dying_since = now
                if sv.death_confirm_seconds > 0:
                    log.warning(
                        "😱 Marín está al borde de la muerte (-%.1f%%): si sigue asi %.0f s, muere",
                        inst.drawdown_pct,
                        sv.death_confirm_seconds,
                    )
            if now - self._dying_since >= sv.death_confirm_seconds:
                await self._die(life, inst)
                return
        else:
            self._dying_since = 0.0
        self.instinct = inst
        # avisa cuando cambia de animo (como mucho cada 10 min, para no llenar Telegram)
        if inst.mood != self._last_mood and time.time() - self._mood_said_at >= MOOD_EVERY:
            if self._last_mood and not inst.dead:
                await self.notifier.send(
                    f"{inst.emoji} Marín está {inst.mood} (miedo {inst.fear:.0%}, "
                    f"{inst.drawdown_pct:.1f}% abajo de su mejor momento)"
                )
                self._mood_said_at = time.time()
            self._last_mood = inst.mood
        if life.dead:
            await self._liquidate()

    async def _die(self, life: Life, inst: Instinct) -> None:
        sv = self.cfg.survival
        life.dead = True
        life.died_at = time.time()
        life.death_reason = (
            f"perdio {inst.drawdown_pct:.1f}% desde su mejor momento "
            f"({inst.peak:.4f} -> {inst.equity:.4f} SOL)"
        )
        self.instinct = survival.compute_instinct(
            life, self.state.positions.values(), sv, self.cfg.risk
        )
        self._last_mood = self.instinct.mood
        self._save()
        log.warning("💀 Marín murió: %s", life.death_reason)
        await self.notifier.send(
            f"💀 Marín MURIÓ (vida {life.number}): {life.death_reason}. "
            f"Vende lo que tenga y no vuelve a comprar. Para darle otra vida: {self._revive_hint()}"
        )
        await self._liquidate()

    async def _liquidate(self) -> None:
        """Al morir vende todo. Una sola vez a la vez, aunque la llamen varios ciclos."""
        if not self.cfg.survival.sell_all_on_death or self._liquidating:
            return
        self._liquidating = True
        try:
            for pos in list(self.state.positions.values()):
                await self.manage_position(
                    pos,
                    pos.last_price_usd or pos.entry_price_usd,
                    None,
                    force_reason="Marín murió: vende todo",
                )
        finally:
            self._liquidating = False

    async def revive(self) -> tuple[bool, str]:
        """Le da otra vida (boton del panel). El historial se conserva. Si algo falla, sigue
        muerta (nunca queda a medias)."""
        life = self.state.life
        if life is None or not life.dead:
            return False, "Marín está viva, no hay nada que revivir"
        try:
            wallet = await self.trader.wallet_sol()
        except Exception as e:  # noqa: BLE001
            return False, f"no se pudo leer el saldo, sigue muerta: {e}"
        capital = self._life_capital(wallet)
        if capital < self.cfg.risk.trade_size_sol:
            return False, f"con {capital:.4f} SOL no alcanza para una vida nueva: deposita SOL"
        if self.state.life is not life:
            return False, "ya revivió"
        await self._birth(life.number + 1, capital)
        await self.check_life()
        self._update_buy_block()
        return True, f"Marín revivió: vida {self.state.life.number}"

    def life_info(self) -> dict | None:
        life, inst = self.state.life, self.instinct
        if life is None or inst is None:
            return None
        return survival.describe(inst, life, self.cfg.survival, self.cfg.risk)

    # ----------------------------------------------------------- compras

    def _update_buy_block(self) -> None:
        """Revisa los limites globales (no los de un token puntual) para mostrar en el panel
        si el bot esta en pausa y por que."""
        ok, why = risk.can_open(
            "__probe__",
            self.state,
            self.last_wallet_sol,
            self.cfg.risk,
            live=self.cfg.live,
            regime_score=self.regime.current.get("score"),
            instinct=self.instinct,
            trade_size=self._trade_size(),
        )
        if ok and not self.state.buy_stages:
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
        while True:
            await asyncio.sleep(self.cfg.filters.recheck_seconds)
            try:
                await self.evaluate_once()
            except Exception:  # noqa: BLE001
                log.exception("Error evaluando candidatos")

    async def evaluate_once(self, now: float | None = None) -> None:
        f = self.cfg.filters
        now = now or time.time()
        self.last_eval_at = now
        await self.check_life()
        self._update_buy_block()
        if self.instinct is not None and self.instinct.dead:
            return  # muerta: ya no analiza monedas
        for mint, cand in list(self.watchlist.items()):
            # los tokens marcados siguen vigilados hasta que compran o expiran
            if mint not in self.armed and now - cand.first_seen > f.watch_minutes * 60:
                self.watchlist.pop(mint, None)
        for mint, armed in list(self.armed.items()):
            if now - armed.armed_at > f.arm_minutes * 60 or mint not in self.watchlist:
                self.armed.pop(mint, None)
        if not self.watchlist:
            return
        try:
            snaps = await self.dex.snapshots(self.watchlist.keys())
        except Exception:  # noqa: BLE001
            log.exception("Error obteniendo datos de DexScreener")
            return

        ranked = []
        for mint, snap in snaps.items():
            cand = self.watchlist.get(mint)
            if not cand:
                continue
            ev = evaluate(snap, f, now)
            self._update_radar(cand, snap, ev.score, ev.verdict.value, ev.reasons, now)
            if ev.verdict is Verdict.REJECT:
                self.watchlist.pop(mint, None)
                self.armed.pop(mint, None)
                self.rejected.add(mint)
                continue
            stage = self.radar[mint]["stage"]
            if stage not in self.state.buy_stages:
                self.armed.pop(mint, None)
                continue
            if f.entry_mode == "momentum":
                if ev.verdict is Verdict.BUY:
                    ranked.append((ev.score, cand, snap, stage, f"score {ev.score}", None, False))
                continue
            armed = self.armed.get(mint)
            if armed is None:
                if ev.verdict is Verdict.BUY:
                    lo = f.pump_pullback_min_pct if ev.pump else f.pullback_min_pct
                    self.armed[mint] = armed = Armed(
                        mint,
                        snap.symbol,
                        snap.price_usd,
                        now,
                        ev.score,
                        status=("🚀 pump sano, " if ev.pump else "")
                        + f"marcada, busca retroceso de -{lo:.0f}%",
                        pump=ev.pump,
                    )
                    log.info(
                        "🎯 Marín marca %s (score %s)%s: espera retroceso",
                        snap.symbol,
                        ev.score,
                        " por excepcion de pump" if ev.pump else "",
                    )
                    self._radar_armed(mint, armed)
                continue
            action = check_pullback(armed, snap, f, now)
            self._radar_armed(mint, armed)
            if action == "drop":
                log.info("Marín descarta %s: %s", armed.symbol, armed.status)
                self.armed.pop(mint, None)
                self.radar[mint]["armed"] = None
            elif action == "buy":
                reason = ("pump: " if armed.pump else "") + armed.status
                ranked.append((armed.score, cand, snap, stage, reason, armed.last_dd, armed.pump))
        ranked.sort(key=lambda r: r[0], reverse=True)
        for score, cand, snap, stage, reason, pullback, pump in ranked:
            await self._try_buy(
                cand, snap, score, stage, reason=reason, pullback_pct=pullback, pump=pump
            )

    def _entry_mode_label(self) -> str:
        f = self.cfg.filters
        if f.entry_mode == "pullback" and f.pullback_bounce_pct > 0:
            return "rebote"  # v3
        return f.entry_mode

    def _radar_armed(self, mint: str, armed: Armed) -> None:
        if mint in self.radar:
            self.radar[mint]["armed"] = {
                "dd": round(armed.last_dd, 1),
                "status": armed.status,
                "high": armed.high,
                "since": armed.armed_at,
                "pump": armed.pump,
            }

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
            "armed": None,
        }
        if cand.mint in self.armed:
            self._radar_armed(cand.mint, self.armed[cand.mint])
        if len(self.radar) > MAX_RADAR:
            by_age = sorted(self.radar.values(), key=lambda r: r["t"])
            for old in by_age[: len(self.radar) - MAX_RADAR]:
                self.radar.pop(old["mint"], None)

    async def _try_buy(
        self,
        cand: Candidate,
        snap: TokenSnapshot,
        score: float,
        stage: str = "",
        reason: str = "",
        pullback_pct: float | None = None,
        pump: bool = False,
    ) -> None:
        wallet = await self.trader.wallet_sol()
        ok, why = self._buy_allowed(cand.mint, wallet, score, pump)
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
                self.armed.pop(cand.mint, None)
                self.rejected.add(cand.mint)
            return

        async with self._trade_lock:
            # mientras revisaba RugCheck pudo cambiar todo (o morir): se revisa otra vez
            wallet = await self.trader.wallet_sol()
            ok, why = self._buy_allowed(cand.mint, wallet, score, pump)
            if not ok:
                log.debug("No se compra %s: %s", snap.symbol, why)
                return
            size = self._trade_size()
            fill = await self.trader.buy(cand.mint, size, snap)
        self.watchlist.pop(cand.mint, None)
        self.armed.pop(cand.mint, None)
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
            entry_snapshot={
                "score": score,
                "mode": "pump" if pump else self._entry_mode_label(),
                "pullback_pct": None if pullback_pct is None else round(pullback_pct, 1),
                "mcap": snap.market_cap_usd,
                "liq": snap.liquidity_usd if snap.liquidity_known else None,
                "chg_m5": snap.change_m5,
                "chg_h1": snap.change_h1,
                "age_min": round(snap.age_minutes(), 1),
                "buys_m5": snap.buys_m5,
                "sells_m5": snap.sells_m5,
                "ratio": round(snap.buy_sell_ratio_m5, 2),
                "vol_m5": snap.volume_m5,
                "vol_h1": snap.volume_h1,
                "dex": snap.dex_id,
                "regime": self.regime.current.get("score"),
                "fear": None if self.instinct is None else round(100 * self.instinct.fear),
            },
        )
        self.state.open_position(pos)
        self.state.record_price(pos.mint, pos.entry_time, snap.price_usd)
        self.state.log_trade(
            "BUY",
            pos,
            fill.sol_amount,
            fill.token_amount,
            snap.price_usd,
            reason or f"score {score}",
            fill.signature,
        )
        self.stats["bought"] += 1
        self._save()
        await self.notifier.send(
            f"🟢 COMPRA {snap.symbol} | {fill.sol_amount:.4f} SOL | score {score} | "
            f"{reason + ' | ' if reason and pullback_pct is not None else ''}"
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
            now = time.time()
            for mint, pos in list(self.state.positions.items()):
                snap = snaps.get(mint)
                if snap:
                    self.state.record_price(mint, now, snap.price_usd)
                    liq = snap.liquidity_usd if snap.liquidity_known else None
                    await self.manage_position(pos, snap.price_usd, liq)
            try:
                await self.check_life()
            except Exception:  # noqa: BLE001
                log.exception("Error revisando la vida de Marín")

    def _buy_allowed(self, mint: str, wallet: float, score: float, pump: bool) -> tuple[bool, str]:
        if self.state.life is not None and self.state.life.dead:
            return False, "💀 Marín murió"
        ok, why = risk.can_open(
            mint,
            self.state,
            wallet,
            self.cfg.risk,
            live=self.cfg.live,
            regime_score=self.regime.current.get("score"),
            instinct=self.instinct,
            trade_size=self._trade_size(),
        )
        if not ok:
            return ok, why
        f = self.cfg.filters
        # un pump tiene que pasar los dos puntajes (scoring.evaluate pide min_score siempre)
        base = max(f.min_score, f.pump_min_score) if pump else f.min_score
        return survival.score_gate(score, base, self.instinct)

    def _trade_size(self) -> float:
        """Tamano de la compra: con miedo, Marín apuesta menos (pero no tan poco que las
        comisiones se coman la operacion: survival.min_trade_sol)."""
        base = self.cfg.risk.trade_size_sol
        if self.instinct is None:
            return base
        floor = min(base, self.cfg.survival.min_trade_sol)
        return max(floor, base * self.instinct.size_mult)

    async def manage_position(
        self, pos: Position, price: float, liquidity: float | None, force_reason: str = ""
    ) -> None:
        if pos.mint in self._selling:  # ya hay una venta en curso para esta posicion
            return
        if self.state.positions.get(pos.mint) is not pos:  # ya se cerro
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
        async with self._trade_lock:
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

    async def _regime_loop(self) -> None:
        while True:
            try:
                if time.time() - self.regime.fetched_at > 600:
                    await self.regime.refresh_market()
                self.regime.update(self.radar)
            except Exception:  # noqa: BLE001
                log.debug("Error calculando el regimen", exc_info=True)
            await asyncio.sleep(60)

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
                self.state.heartbeat()
                await self.check_life()
            except Exception:  # noqa: BLE001
                log.debug("No se pudo registrar el equity", exc_info=True)
            await asyncio.sleep(EQUITY_EVERY_SECONDS)
