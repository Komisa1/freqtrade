"""Reglas de salida. Funcion pura: dada una posicion y el precio actual, que hacer."""

from __future__ import annotations

from dataclasses import dataclass

from .config import ExitConfig
from .models import Position


@dataclass
class ExitAction:
    fraction_of_remaining: float  # 1.0 = vender todo lo que queda
    reason: str
    tp_index: int | None = None


def decide_exit(
    pos: Position, price: float, liquidity_usd: float | None, now: float, cfg: ExitConfig
) -> ExitAction | None:
    if pos.closed or pos.tokens_left <= 0 or price <= 0:
        return None
    mult = pos.multiple(price)
    held_min = (now - pos.entry_time) / 60

    if liquidity_usd is not None and liquidity_usd < cfg.min_liquidity_usd_exit:
        return ExitAction(1.0, f"liquidez cayo a ${liquidity_usd:,.0f} (posible rug)")

    # stop loss; despues de la primera toma de ganancias el stop sube a la entrada (breakeven)
    stop_mult = 1.0 if pos.tp_done else 1 - cfg.stop_loss_pct / 100
    if mult <= stop_mult:
        label = "breakeven" if pos.tp_done else f"stop loss -{cfg.stop_loss_pct:.0f}%"
        return ExitAction(1.0, f"{label} ({mult:.2f}x)")

    peak_mult = pos.multiple(max(pos.peak_price_usd, price))
    if peak_mult >= cfg.trailing_activate_multiple:
        drawdown = (1 - price / max(pos.peak_price_usd, price)) * 100
        if drawdown >= cfg.trailing_stop_pct:
            return ExitAction(
                1.0, f"trailing stop: -{drawdown:.0f}% desde maximo {peak_mult:.2f}x ({mult:.2f}x)"
            )

    for i, (target, pct) in enumerate(cfg.take_profit_ladder):
        if i in pos.tp_done or mult < target:
            continue
        sold_pct = sum(cfg.take_profit_ladder[j][1] for j in pos.tp_done)
        remaining_pct = max(100 - sold_pct, 1e-9)
        frac = min(1.0, pct / remaining_pct)
        return ExitAction(frac, f"take profit {target:g}x ({pct:g}% de la posicion)", tp_index=i)

    if held_min >= cfg.max_hold_minutes:
        return ExitAction(1.0, f"tiempo maximo ({held_min:.0f} min, {mult:.2f}x)")
    if held_min >= cfg.stale_minutes and peak_mult < 1 + cfg.stale_min_gain_pct / 100:
        return ExitAction(1.0, f"sin movimiento en {held_min:.0f} min ({mult:.2f}x)")
    return None
