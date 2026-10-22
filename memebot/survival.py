"""Instinto de supervivencia: Marín tiene una vida y le da miedo perderla.

- Cada vida empieza con un capital (lo que tiene al nacer, o `survival.life_capital_sol`).
- Su patrimonio de vida = capital + lo que gano o perdio con las monedas que compro en esta
  vida (las abiertas cuentan a su valor actual).
- El miedo (0-100 %) crece mientras mas cae ese patrimonio desde su mejor momento y con cada
  perdida seguida. Con miedo apuesta menos, exige mas puntaje, abre menos posiciones a la
  vez, descansa mas despues de perder y no compra si el mercado esta en contra.
- Si el patrimonio cae `death_drawdown_pct` desde su mejor momento, muere: vende todo y no
  vuelve a comprar nunca, aunque reinicies el bot. Solo tu puedes darle otra vida
  (`python -m memebot revivir` o el boton del panel).

El mejor momento solo cuenta ganancias ya cobradas: si una moneda sube y luego regresa, eso
no le da miedo; perder dinero (cobrado o no) si.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from .config import RiskConfig, SurvivalConfig
from .models import Position


@dataclass
class Life:
    number: int = 1
    born_at: float = 0.0  # 0 = todavia no nace (recien revivida): nace al arrancar el bot
    capital: float = 0.0
    realized_sol: float = 0.0  # ganancia/perdida cobrada de las monedas de esta vida
    peak: float = 0.0  # mejor patrimonio cobrado (capital + realized_sol)
    loss_streak: int = 0
    trades: int = 0
    dead: bool = False
    died_at: float = 0.0
    death_reason: str = ""

    @property
    def born(self) -> bool:
        return self.born_at > 0


@dataclass
class Instinct:
    """Como se siente Marín ahorita y que tan cuidadosa la vuelve eso."""

    fear: float  # 0..1
    drawdown_pct: float  # caida desde su mejor momento
    equity: float
    peak: float
    death_line: float  # patrimonio con el que muere
    size_mult: float
    score_bonus: float
    max_open: int
    cooldown_minutes: float
    min_regime: float  # 0 = no le importa el mercado
    mood: str
    emoji: str
    dead: bool = False


MOODS = (  # (miedo menor a, nombre, emoji)
    (0.15, "tranquila", "😌"),
    (0.40, "nerviosa", "😬"),
    (0.70, "asustada", "😨"),
    (1.01, "aterrada", "😱"),
)


def mood_of(fear: float, dead: bool = False) -> tuple[str, str]:
    if dead:
        return "muerta", "💀"
    for limit, name, emoji in MOODS:
        if fear < limit:
            return name, emoji
    return MOODS[-1][1], MOODS[-1][2]


def belongs(pos: Position, life: Life) -> bool:
    return life.born and pos.entry_time >= life.born_at


def life_equity(life: Life, open_positions: Iterable[Position]) -> float:
    """Capital + lo cobrado en esta vida + lo que valen hoy sus posiciones abiertas."""
    unrealized = 0.0
    for p in open_positions:
        if belongs(p, life):
            price = p.last_price_usd or p.entry_price_usd
            unrealized += p.sol_received + p.unrealized_value_sol(price) - p.sol_spent
    return life.capital + life.realized_sol + unrealized


def drawdown_pct(equity: float, peak: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, (1 - equity / peak) * 100)


def fear_level(dd_pct: float, loss_streak: int, cfg: SurvivalConfig) -> float:
    span = cfg.death_drawdown_pct - cfg.fear_start_pct
    dd_fear = min(1.0, max(0.0, (dd_pct - cfg.fear_start_pct) / span))
    streak_fear = min(cfg.streak_fear_max, max(0, loss_streak) * cfg.streak_fear_per_loss)
    return 1 - (1 - dd_fear) * (1 - streak_fear)


def compute_instinct(
    life: Life, open_positions: Iterable[Position], cfg: SurvivalConfig, risk: RiskConfig
) -> Instinct:
    equity = life_equity(life, open_positions)
    peak = max(life.peak, life.capital + life.realized_sol)
    dd = drawdown_pct(equity, peak)
    fear = 1.0 if life.dead else fear_level(dd, life.loss_streak, cfg)
    mood, emoji = mood_of(fear, life.dead)
    return Instinct(
        fear=fear,
        drawdown_pct=dd,
        equity=equity,
        peak=peak,
        death_line=peak * (1 - cfg.death_drawdown_pct / 100),
        size_mult=1 - fear * (1 - cfg.min_size_pct / 100),
        score_bonus=fear * cfg.fear_score_bonus,
        max_open=max(1, math.ceil(risk.max_open_positions * (1 - fear) - 1e-9)),
        cooldown_minutes=risk.cooldown_minutes_after_loss
        * (1 + fear * (cfg.fear_cooldown_mult - 1)),
        min_regime=cfg.fear_regime_min if fear >= cfg.fear_regime_from else 0.0,
        mood=mood,
        emoji=emoji,
        dead=life.dead,
    )


def should_die(instinct: Instinct, cfg: SurvivalConfig) -> bool:
    return not instinct.dead and instinct.drawdown_pct >= cfg.death_drawdown_pct


def record_close(life: Life | None, pos: Position) -> None:
    """Anota en la vida una posicion que se cerro (solo si la compro en esta vida)."""
    if life is None or not belongs(pos, life):
        return
    pnl = pos.realized_pnl_sol
    life.realized_sol += pnl
    life.trades += 1
    life.loss_streak = life.loss_streak + 1 if pnl < 0 else 0
    life.peak = max(life.peak, life.capital + life.realized_sol)


def score_gate(score: float, base_min: float, instinct: Instinct | None) -> tuple[bool, str]:
    if instinct is None or instinct.score_bonus <= 0:
        return True, ""
    need = base_min + instinct.score_bonus
    if score < need:
        return False, f"miedo {instinct.fear:.0%}: puntaje {score:.0f} < {need:.0f}"
    return True, ""


def describe(instinct: Instinct, life: Life, cfg: SurvivalConfig, risk: RiskConfig) -> dict:
    """Datos de la vida para el panel."""
    to_death = instinct.equity - instinct.death_line
    return {
        "enabled": True,
        "number": life.number,
        "born_at": life.born_at,
        "dead": life.dead,
        "died_at": life.died_at,
        "death_reason": life.death_reason,
        "capital": life.capital,
        "equity": instinct.equity,
        "peak": instinct.peak,
        "death_line": instinct.death_line,
        "to_death_sol": to_death,
        "life_left_pct": (
            0.0
            if life.dead or instinct.peak <= instinct.death_line
            else max(0.0, min(100.0, 100 * to_death / (instinct.peak - instinct.death_line)))
        ),
        "drawdown_pct": instinct.drawdown_pct,
        "death_drawdown_pct": cfg.death_drawdown_pct,
        "pnl_sol": instinct.equity - life.capital,
        "fear": instinct.fear,
        "mood": instinct.mood,
        "emoji": instinct.emoji,
        "loss_streak": life.loss_streak,
        "trades": life.trades,
        "effects": {
            "trade_size_sol": risk.trade_size_sol * instinct.size_mult,
            "base_trade_size_sol": risk.trade_size_sol,
            "score_bonus": instinct.score_bonus,
            "max_open": instinct.max_open,
            "base_max_open": risk.max_open_positions,
            "cooldown_minutes": instinct.cooldown_minutes,
            "min_regime": instinct.min_regime,
        },
    }
