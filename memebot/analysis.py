"""Analisis de resultados: que tenian en comun las ganadoras vs las perdedoras al comprarlas.

Sirve para ajustar los filtros con datos reales en lugar de adivinar.
"""

from __future__ import annotations

from collections.abc import Callable
from statistics import median
from typing import Any

from .models import Position

FEATURES: list[tuple[str, str]] = [
    ("score", "Puntaje"),
    ("pullback_pct", "Retroceso al comprar (%)"),
    ("chg_m5", "Cambio 5 min (%)"),
    ("chg_h1", "Cambio 1 hora (%)"),
    ("mcap", "Market cap ($)"),
    ("liq", "Liquidez ($)"),
    ("age_min", "Edad (min)"),
    ("ratio", "Compras/ventas 5 min"),
    ("regime", "Regimen del mercado (0-100)"),
]
MODE_LABELS = {
    "rebote": "Rebote confirmado (v3)",
    "pullback": "Retroceso (v2)",
    "pump": "Pump con excepcion",
    "momentum": "Momentum",
    "": "Sin datos (v1)",
}


def group_stats(
    closed: list[Position], key: Callable[[Position], str], labels: dict[str, str]
) -> list[dict[str, Any]]:
    groups: dict[str, list[Position]] = {}
    for p in closed:
        groups.setdefault(key(p), []).append(p)
    out = []
    for k, ps in groups.items():
        spent = sum(p.sol_spent for p in ps)
        pnl = sum(p.realized_pnl_sol for p in ps)
        out.append(
            {
                "key": k,
                "label": labels.get(k, k or "—"),
                "trades": len(ps),
                "wins": sum(1 for p in ps if p.realized_pnl_sol > 0),
                "pnl_sol": pnl,
                "roi_pct": 100 * pnl / spent if spent else 0.0,
                "best_x": max((p.sol_received / p.sol_spent for p in ps if p.sol_spent), default=0),
            }
        )
    order = list(labels)
    return sorted(out, key=lambda r: order.index(r["key"]) if r["key"] in order else 99)


def mode_stats(closed: list[Position]) -> list[dict[str, Any]]:
    return group_stats(closed, lambda p: p.entry_snapshot.get("mode", ""), MODE_LABELS)


def compare_winners_losers(closed: list[Position]) -> dict[str, Any]:
    """Mediana de cada dato de entrada para ganadoras y perdedoras (solo trades con datos)."""
    with_data = [p for p in closed if p.entry_snapshot]
    wins = [p for p in with_data if p.realized_pnl_sol > 0]
    losses = [p for p in with_data if p.realized_pnl_sol <= 0]

    def med(ps: list[Position], k: str) -> float | None:
        vals = [p.entry_snapshot.get(k) for p in ps]
        vals = [v for v in vals if isinstance(v, int | float)]
        return round(median(vals), 2) if vals else None

    rows = [
        {"key": k, "label": label, "win": med(wins, k), "loss": med(losses, k)}
        for k, label in FEATURES
    ]
    rows = [r for r in rows if r["win"] is not None or r["loss"] is not None]
    return {"wins": len(wins), "losses": len(losses), "rows": rows}
