"""Entrada en retroceso (pullback): no comprar en el pico, sino cuando la moneda baja un poco
despues de calificar y todavia tiene compradores.

Por que: en la simulacion v1 casi todas las perdidas fueron stop loss justo despues de comprar,
o sea, Marin compraba en el tope de la vela. Esperar un retroceso suele dar mejor precio y
filtra las monedas que se desploman en cuanto dejan de subir (esas pasan el maximo permitido
y se descartan sin comprar).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import FilterConfig
from .models import TokenSnapshot


@dataclass
class Armed:
    mint: str
    symbol: str
    high: float  # maximo visto desde que se marco
    armed_at: float
    score: float
    last_dd: float = 0.0  # retroceso actual en %
    status: str = ""
    pump: bool = False  # entro por la excepcion de pump: pide un retroceso mas profundo


def check_pullback(armed: Armed, snap: TokenSnapshot, cfg: FilterConfig, now: float) -> str:
    """Devuelve "buy", "wait" o "drop" y deja el motivo en armed.status."""
    lo, hi = (
        (cfg.pump_pullback_min_pct, cfg.pump_pullback_max_pct)
        if armed.pump
        else (cfg.pullback_min_pct, cfg.pullback_max_pct)
    )
    armed.high = max(armed.high, snap.price_usd)
    dd = (1 - snap.price_usd / armed.high) * 100 if armed.high else 0.0
    armed.last_dd = dd
    if now - armed.armed_at > cfg.arm_minutes * 60:
        armed.status = f"no retrocedio en {cfg.arm_minutes:.0f} min"
        return "drop"
    if dd > hi:
        armed.status = f"se desplomo {dd:.0f}% desde el maximo"
        return "drop"
    if snap.liquidity_known and snap.liquidity_usd < cfg.min_liquidity_usd:
        armed.status = f"liquidez baja (${snap.liquidity_usd:,.0f})"
        return "wait"
    if dd < lo:
        armed.status = f"esperando retroceso: va -{dd:.0f}% (busca -{lo:.0f}%)"
        return "wait"
    if snap.buy_sell_ratio_m5 < cfg.pullback_min_buy_sell_ratio:
        armed.status = f"retroceso sin compradores ({snap.buys_m5}/{snap.sells_m5})"
        return "wait"
    armed.status = f"retroceso de {dd:.0f}% desde el maximo"
    return "buy"
