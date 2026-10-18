"""Etapas de un token de pump.fun, como las columnas "Trenches" de GMGN.

- new: recien creado, todavia en la bonding curve de pump.fun
- about: en la bonding curve pero cerca de completarla (a punto de migrar)
- migrated: ya completo la curva y opera en un DEX (PumpSwap/Raydium/...)

El progreso de la curva se estima con la formula de producto constante de pump.fun:
reservas virtuales iniciales 30 SOL / 1,073M tokens, y migra cuando hay ~85 SOL reales.
Si pump.fun cambia esos parametros, el progreso seria solo aproximado.
"""

from __future__ import annotations

import math

STAGES = ("new", "about", "migrated")
STAGE_LABELS = {"new": "Nuevas", "about": "A punto de migrar", "migrated": "Migradas"}

VIRTUAL_SOL_START = 30.0
REAL_SOL_TO_MIGRATE = 85.0
VIRTUAL_TOKENS_START = 1_073_000_000
TOTAL_SUPPLY = 1_000_000_000
K = VIRTUAL_SOL_START * VIRTUAL_TOKENS_START  # constante x*y=k
ABOUT_TO_MIGRATE = 0.65  # a partir de 65% de la curva se considera "a punto de migrar"


def progress_from_vsol(vsol: float) -> float:
    return max(0.0, min(1.0, (vsol - VIRTUAL_SOL_START) / REAL_SOL_TO_MIGRATE))


def progress_from_mcap_sol(mcap_sol: float) -> float:
    """mcap = precio * supply = (vsol^2 / k) * supply  =>  vsol = sqrt(mcap * k / supply)"""
    if mcap_sol <= 0:
        return 0.0
    return progress_from_vsol(math.sqrt(mcap_sol * K / TOTAL_SUPPLY))


def classify(dex_id: str, source: str, progress: float | None, migrated_seen: bool = False) -> str:
    on_curve = dex_id == "pumpfun"
    if migrated_seen or source == "pumpfun-graduated" or (dex_id and not on_curve):
        return "migrated"
    if progress is not None and progress >= ABOUT_TO_MIGRATE:
        return "about"
    return "new"
