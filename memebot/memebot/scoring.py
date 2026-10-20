"""Filtros y puntaje de oportunidades. Funciones puras (sin red) para poder testearlas.

La idea: buscar tokens con impulso real (mas compras que ventas, volumen alto respecto a su
liquidez, precio subiendo) pero sin perseguir velas que ya hicieron x10 en una hora.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .config import FilterConfig
from .models import TokenSnapshot


class Verdict(str, Enum):
    BUY = "BUY"  # cumple todo: candidato a compra (falta RugCheck y riesgo)
    WAIT = "WAIT"  # todavia no, re-evaluar mas tarde
    REJECT = "REJECT"  # descartado para siempre


@dataclass
class Evaluation:
    verdict: Verdict
    score: float
    reasons: list[str] = field(default_factory=list)


def _lin(x: float, lo: float, hi: float, points: float) -> float:
    """0 puntos en `lo` o menos, `points` en `hi` o mas, lineal en el medio."""
    if hi == lo:
        return points if x >= hi else 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo))) * points


def score_snapshot(s: TokenSnapshot) -> float:
    score = 0.0
    score += _lin(s.buy_sell_ratio_m5, 1.0, 2.5, 25)  # presion compradora
    base = s.liquidity_usd if s.liquidity_known else s.market_cap_usd * 0.25
    if base > 0:
        score += _lin(s.volume_h1 / base, 0.5, 5.0, 20)  # rotacion del dinero
    # impulso de 5 min: premia subir, pero castiga parabolicas (>60% en 5 min)
    if s.change_m5 <= 60:
        score += _lin(s.change_m5, 0, 30, 20)
    else:
        score += max(0.0, 20 - (s.change_m5 - 60) / 3)
    # tendencia de 1 hora
    if s.change_h1 <= 300:
        score += _lin(s.change_h1, 10, 150, 15)
    score += _lin(s.txns_m5, 25, 300, 10)  # actividad
    if not s.liquidity_known and s.dex_id == "pumpfun":
        score += 10  # bonding curve de pump.fun: la liquidez la da la propia curva
    elif s.market_cap_usd > 0 and s.liquidity_usd / s.market_cap_usd >= 0.08:
        score += 10  # liquidez sana respecto al market cap
    return round(score, 1)


def evaluate(s: TokenSnapshot, cfg: FilterConfig, now: float | None = None) -> Evaluation:
    now = now or time.time()
    age = s.age_minutes(now)
    score = score_snapshot(s)

    # descartes definitivos
    hard = []
    if age > cfg.max_age_minutes:
        hard.append(f"muy viejo ({age:.0f} min)")
    if s.market_cap_usd > cfg.max_market_cap_usd:
        hard.append(f"market cap alto (${s.market_cap_usd:,.0f})")
    if s.liquidity_usd > cfg.max_liquidity_usd:
        hard.append(f"liquidez fuera de rango (${s.liquidity_usd:,.0f})")
    if hard:
        return Evaluation(Verdict.REJECT, score, hard)

    # condiciones que pueden cumplirse mas adelante
    soft = []
    if age < cfg.min_age_minutes:
        soft.append(f"muy nuevo ({age:.1f} min)")
    if s.liquidity_known and s.liquidity_usd < cfg.min_liquidity_usd:
        soft.append(f"poca liquidez (${s.liquidity_usd:,.0f})")
    if s.market_cap_usd < cfg.min_market_cap_usd:
        soft.append(f"market cap bajo (${s.market_cap_usd:,.0f})")
    if s.volume_m5 < cfg.min_volume_m5_usd:
        soft.append(f"poco volumen 5m (${s.volume_m5:,.0f})")
    if s.txns_m5 < cfg.min_txns_m5:
        soft.append(f"pocas txns 5m ({s.txns_m5})")
    if s.buy_sell_ratio_m5 < cfg.min_buy_sell_ratio_m5:
        soft.append(f"compras/ventas 5m bajo ({s.buy_sell_ratio_m5:.2f})")
    if s.change_h1 > cfg.max_price_change_h1_pct:
        soft.append(f"ya subio demasiado ({s.change_h1:.0f}% en 1h)")
    if cfg.entry_mode == "momentum" and s.change_m5 > cfg.max_price_change_m5_pct:
        soft.append(f"subiendo muy rapido ({s.change_m5:.0f}% en 5m)")
    if s.change_m5 < cfg.min_price_change_m5_pct:
        soft.append(f"cayendo ({s.change_m5:.0f}% en 5m)")
    if score < cfg.min_score:
        soft.append(f"puntaje {score} < {cfg.min_score}")
    if soft:
        return Evaluation(Verdict.WAIT, score, soft)
    return Evaluation(Verdict.BUY, score, [f"puntaje {score}"])
