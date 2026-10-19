"""Gestion de riesgo global: cuanto y cuando se permite comprar."""

from __future__ import annotations

import time

from .config import RiskConfig
from .state import State


def can_open(
    mint: str,
    state: State,
    wallet_sol: float,
    cfg: RiskConfig,
    now: float | None = None,
    live: bool = True,
) -> tuple[bool, str]:
    now = now or time.time()
    if mint in state.positions:
        return False, "ya hay una posicion abierta en este token"
    if cfg.never_rebuy_same_token and mint in state.traded_mints:
        return False, "token ya operado antes"
    if len(state.positions) >= cfg.max_open_positions:
        return False, f"maximo de posiciones abiertas ({cfg.max_open_positions})"
    pnl_today = state.realized_pnl_today(now)
    if pnl_today <= -cfg.max_daily_loss_sol:
        return False, f"limite de perdida diaria alcanzado ({pnl_today:.3f} SOL)"
    if state.last_loss_time and now - state.last_loss_time < cfg.cooldown_minutes_after_loss * 60:
        return False, "enfriamiento despues de una perdida"
    # el tope de perdida total protege tu dinero real; en simulacion no se aplica para que
    # el bot siga juntando datos aunque el historial simulado vaya en perdida
    if live and cfg.max_total_loss_sol > 0:
        total = sum(p.realized_pnl_sol for p in state.closed)
        if total <= -cfg.max_total_loss_sol:
            return False, f"tope de perdida total alcanzado ({total:.3f} SOL)"
    if cfg.max_capital_sol > 0:
        in_use = sum(p.sol_spent - p.sol_received for p in state.positions.values())
        if max(0.0, in_use) + cfg.trade_size_sol > cfg.max_capital_sol:
            return False, f"tope de capital del bot ({cfg.max_capital_sol} SOL) en uso"
    if wallet_sol - cfg.trade_size_sol < cfg.min_sol_reserve:
        return False, f"saldo insuficiente ({wallet_sol:.3f} SOL)"
    return True, ""
