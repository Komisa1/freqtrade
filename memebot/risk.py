"""Gestion de riesgo global: cuanto y cuando se permite comprar."""

from __future__ import annotations

import time

from .config import RiskConfig
from .state import State
from .survival import Instinct


def can_open(
    mint: str,
    state: State,
    wallet_sol: float,
    cfg: RiskConfig,
    now: float | None = None,
    live: bool = True,
    regime_score: float | None = None,
    instinct: Instinct | None = None,
    trade_size: float | None = None,
) -> tuple[bool, str]:
    """`instinct` (miedo de Marín) endurece los limites; `trade_size` es el tamano real de la
    compra (con miedo es menor que risk.trade_size_sol)."""
    now = now or time.time()
    size = cfg.trade_size_sol if trade_size is None else trade_size
    max_open = cfg.max_open_positions
    cooldown = cfg.cooldown_minutes_after_loss
    if instinct is not None:
        if instinct.dead:
            return False, "💀 Marín murió: ya no compra (dale otra vida desde el panel)"
        max_open = min(max_open, instinct.max_open)
        cooldown = max(cooldown, instinct.cooldown_minutes)
    if mint in state.positions:
        return False, "ya hay una posicion abierta en este token"
    if cfg.never_rebuy_same_token and mint in state.traded_mints:
        return False, "token ya operado antes"
    if len(state.positions) >= max_open:
        if max_open < cfg.max_open_positions:
            return False, f"miedo {instinct.fear:.0%}: maximo {max_open} posicion(es) a la vez"
        return False, f"maximo de posiciones abiertas ({max_open})"
    # los limites de perdida (diaria y total) protegen tu dinero real; en simulacion no se
    # aplican para que el bot siga juntando datos aunque vaya en perdida
    pnl_today = state.realized_pnl_today(now)
    if live and pnl_today <= -cfg.max_daily_loss_sol:
        return False, f"limite de perdida diaria alcanzado ({pnl_today:.3f} SOL)"
    if state.last_loss_time and now - state.last_loss_time < cooldown * 60:
        left = (cooldown * 60 - (now - state.last_loss_time)) / 60
        if cooldown > cfg.cooldown_minutes_after_loss:
            return False, f"miedo {instinct.fear:.0%}: descansa tras perder ({left:.0f} min)"
        return False, "enfriamiento despues de una perdida"
    if live and cfg.max_total_loss_sol > 0:
        total = sum(p.realized_pnl_sol for p in state.closed)
        if total <= -cfg.max_total_loss_sol:
            return False, f"tope de perdida total alcanzado ({total:.3f} SOL)"
    if live and cfg.max_consecutive_losses > 0 and state.closed:
        streak = 0
        for p in reversed(state.closed):
            if p.realized_pnl_sol > 0:
                break
            streak += 1
        last = state.closed[-1].close_time
        if streak >= cfg.max_consecutive_losses and now - last < cfg.streak_pause_minutes * 60:
            left = (cfg.streak_pause_minutes * 60 - (now - last)) / 60
            return False, f"racha de {streak} perdidas seguidas: pausa ({left:.0f} min)"
    if (
        cfg.regime_min_score > 0
        and regime_score is not None
        and regime_score < cfg.regime_min_score
    ):
        return False, f"mercado en contra (regimen {regime_score:.0f} < {cfg.regime_min_score:.0f})"
    if (
        instinct is not None
        and instinct.min_regime > 0
        and regime_score is not None
        and regime_score < instinct.min_regime
    ):
        return (
            False,
            f"miedo {instinct.fear:.0%}: mercado en contra "
            f"(regimen {regime_score:.0f} < {instinct.min_regime:.0f})",
        )
    if cfg.max_capital_sol > 0:
        in_use = sum(p.sol_spent - p.sol_received for p in state.positions.values())
        # margen del 3% para que las comisiones de cada compra no bloqueen la ultima posicion
        if max(0.0, in_use) + size > cfg.max_capital_sol * 1.03:
            return False, f"tope de capital del bot ({cfg.max_capital_sol} SOL) en uso"
    if wallet_sol - size < cfg.min_sol_reserve:
        return False, f"saldo insuficiente ({wallet_sol:.3f} SOL)"
    return True, ""
