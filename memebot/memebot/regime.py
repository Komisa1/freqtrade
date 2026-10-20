"""Regimen del mercado: ¿el mercado esta a favor o en contra de comprar meme coins ahorita?

Inspirado en `crypto-regime-analyzer` de tradermonty/claude-trading-skills (MIT), adaptado a
meme coins de Solana. Puntaje 0-100 (100 = muy a favor) con cuatro partes:

- Tendencia de SOL (30%): precio contra su promedio de 7 y 30 dias.
- SOL hoy (20%): cambio de 24 h.
- BTC hoy (15%): cambio de 24 h (el resto del mercado cripto suele seguirlo).
- Meme coins del radar (35%): que proporcion de las monedas que Marin esta evaluando van
  subiendo en la ultima hora. Es el dato mas directo de como esta "la trinchera".

Si falta alguna parte, su peso se reparte entre las demas. Por defecto solo se MIDE y se
guarda en cada compra; filtrar con el (risk.regime_min_score) se decide con datos.
"""

from __future__ import annotations

import logging
import time
from statistics import mean
from typing import Any

import aiohttp

from .http import RateLimiter, get_json

log = logging.getLogger(__name__)

CG = "https://api.coingecko.com/api/v3"
_cg_limiter = RateLimiter(10)  # la API gratis de CoinGecko es estricta
WEIGHTS = {"sol_trend": 0.30, "sol_24h": 0.20, "btc_24h": 0.15, "meme_breadth": 0.35}
LABELS = {
    "sol_trend": "Tendencia de SOL",
    "sol_24h": "SOL hoy",
    "btc_24h": "BTC hoy",
    "meme_breadth": "Meme coins subiendo",
}
MIN_BREADTH_SAMPLE = 20


def _lin(x: float, lo: float, hi: float) -> float:
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def sol_trend_score(prices: list[float]) -> float | None:
    if len(prices) < 8:
        return None
    last = prices[-1]
    above7 = last > mean(prices[-7:])
    above30 = last > mean(prices[-30:])
    if above7 and above30:
        return 100.0
    if above7:
        return 60.0
    if above30:
        return 40.0
    return 10.0


def meme_breadth(radar: dict[str, dict], now: float, window: float = 1800) -> float | None:
    """% de monedas del radar (evaluadas en los ultimos 30 min) que van subiendo en 1 h."""
    recent = [r for r in radar.values() if now - r.get("t", 0) <= window]
    if len(recent) < MIN_BREADTH_SAMPLE:
        return None
    up = sum(1 for r in recent if (r.get("chg_h1") or 0) > 0)
    return 100 * up / len(recent)


def score_regime(
    sol_prices: list[float] | None,
    sol_24h: float | None,
    btc_24h: float | None,
    breadth_pct: float | None,
) -> dict[str, Any]:
    parts: dict[str, float | None] = {
        "sol_trend": sol_trend_score(sol_prices or []),
        "sol_24h": None if sol_24h is None else _lin(sol_24h, -8, 8),
        "btc_24h": None if btc_24h is None else _lin(btc_24h, -5, 5),
        "meme_breadth": None if breadth_pct is None else _lin(breadth_pct, 30, 70),
    }
    known = {k: v for k, v in parts.items() if v is not None}
    if not known:
        return {"score": None, "zone": "desconocido", "parts": parts}
    total_w = sum(WEIGHTS[k] for k in known)
    score = round(sum(WEIGHTS[k] * v for k, v in known.items()) / total_w, 1)
    zone = "a favor" if score >= 65 else "neutral" if score >= 40 else "en contra"
    return {
        "score": score,
        "zone": zone,
        "parts": {k: (None if v is None else round(v, 1)) for k, v in parts.items()},
        "raw": {"sol_24h": sol_24h, "btc_24h": btc_24h, "meme_breadth_pct": breadth_pct},
    }


class RegimeTracker:
    """Consulta CoinGecko cada pocos minutos y combina con el radar de Marin."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.sol_prices: list[float] = []
        self.sol_24h: float | None = None
        self.btc_24h: float | None = None
        self.fetched_at = 0.0
        self.current: dict[str, Any] = {"score": None, "zone": "desconocido", "parts": {}}

    async def refresh_market(self) -> None:
        try:
            simple = await get_json(
                self.session,
                f"{CG}/simple/price",
                params={
                    "ids": "solana,bitcoin",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
                limiter=_cg_limiter,
                retries=2,
            )
            self.sol_24h = simple.get("solana", {}).get("usd_24h_change")
            self.btc_24h = simple.get("bitcoin", {}).get("usd_24h_change")
            chart = await get_json(
                self.session,
                f"{CG}/coins/solana/market_chart",
                params={"vs_currency": "usd", "days": "30", "interval": "daily"},
                limiter=_cg_limiter,
                retries=2,
            )
            self.sol_prices = [float(p[1]) for p in chart.get("prices", [])]
            self.fetched_at = time.time()
        except Exception as e:  # noqa: BLE001 - sin CoinGecko se usa solo el radar
            log.debug("CoinGecko no disponible: %s", e)

    def update(self, radar: dict[str, dict], now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        self.current = score_regime(
            self.sol_prices, self.sol_24h, self.btc_24h, meme_breadth(radar, now)
        )
        self.current["t"] = now
        return self.current
