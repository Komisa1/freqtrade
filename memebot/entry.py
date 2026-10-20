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
    low: float = 0.0  # minimo despues del ultimo maximo (para confirmar el rebote)
    bounce: float = 0.0  # rebote actual desde ese minimo, en %


def check_pullback(armed: Armed, snap: TokenSnapshot, cfg: FilterConfig, now: float) -> str:
    """Devuelve "buy", "wait" o "drop" y deja el motivo en armed.status."""
    lo, hi = (
        (cfg.pump_pullback_min_pct, cfg.pump_pullback_max_pct)
        if armed.pump
        else (cfg.pullback_min_pct, cfg.pullback_max_pct)
    )
    price = snap.price_usd
    if price >= armed.high or not armed.low:
        armed.high = max(armed.high, price)
        armed.low = price  # nuevo maximo: el minimo se reinicia
    armed.low = min(armed.low, price)
    dd = (1 - price / armed.high) * 100 if armed.high else 0.0  # caida actual
    depth = (1 - armed.low / armed.high) * 100 if armed.high else 0.0  # que tanto bajo
    armed.bounce = (price / armed.low - 1) * 100 if armed.low else 0.0
    armed.last_dd = depth
    if now - armed.armed_at > cfg.arm_minutes * 60:
        armed.status = f"no retrocedio en {cfg.arm_minutes:.0f} min"
        return "drop"
    if dd > hi:
        armed.status = f"se desplomo {dd:.0f}% desde el maximo"
        return "drop"
    if snap.liquidity_known and snap.liquidity_usd < cfg.min_liquidity_usd:
        armed.status = f"liquidez baja (${snap.liquidity_usd:,.0f})"
        return "wait"
    if depth < lo:
        armed.status = f"esperando retroceso: va -{dd:.0f}% (busca -{lo:.0f}%)"
        return "wait"
    # v3: no comprar mientras sigue cayendo; esperar a que rebote desde el minimo.
    # (en la simulacion v2, las perdedoras se compraban todavia cayendo: -3% en 5 min)
    if not armed.pump and cfg.pullback_bounce_pct > 0 and armed.bounce < cfg.pullback_bounce_pct:
        armed.status = (
            f"bajo -{depth:.0f}%, esperando rebote de +{cfg.pullback_bounce_pct:.0f}% "
            f"(va +{armed.bounce:.1f}%)"
        )
        return "wait"
    if not armed.pump and snap.change_m5 < cfg.pullback_min_change_m5_pct:
        armed.status = f"todavia cayendo ({snap.change_m5:.0f}% en 5 min)"
        return "wait"
    if snap.buy_sell_ratio_m5 < cfg.pullback_min_buy_sell_ratio:
        armed.status = f"retroceso sin compradores ({snap.buys_m5}/{snap.sells_m5})"
        return "wait"
    if armed.pump or cfg.pullback_bounce_pct <= 0:
        armed.status = f"retroceso de {depth:.0f}% desde el maximo"
    else:
        armed.status = f"rebote de +{armed.bounce:.0f}% tras bajar -{depth:.0f}%"
    return "buy"
