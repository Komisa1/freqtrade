"""Datos para la grafica "Mi operacion": velas del token + donde compro y vendio el bot.

Las velas vienen de GeckoTerminal (API publica y gratuita). Si no tiene ese par, se arman
con los precios que el bot fue guardando mientras tenia la posicion abierta.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

import aiohttp

from .http import RateLimiter, get_json

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger(__name__)

GECKO_URL = "https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{tf}"
_gecko_limiter = RateLimiter(25)  # el plan gratis permite ~30 llamadas por minuto
_cache: dict[tuple, tuple[float, list]] = {}
CACHE_SECONDS = 20
BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,48}$")


def pick_timeframe(span_seconds: float) -> tuple[str, int, int]:
    """(timeframe de GeckoTerminal, agregado, segundos por vela)"""
    if span_seconds <= 5 * 3600:
        return "minute", 1, 60
    if span_seconds <= 24 * 3600:
        return "minute", 5, 300
    if span_seconds <= 3 * 86400:
        return "minute", 15, 900
    return "hour", 1, 3600


def candles_from_samples(samples: list[tuple[float, float]], bucket: int) -> list[list[float]]:
    out: list[list[float]] = []
    for t, p in sorted(samples):
        b = int(t // bucket * bucket)
        if out and out[-1][0] == b:
            c = out[-1]
            c[2] = max(c[2], p)
            c[3] = min(c[3], p)
            c[4] = p
        else:
            out.append([b, p, p, p, p])
    return out


async def gecko_candles(
    session: aiohttp.ClientSession, pool: str, start: float, end: float
) -> list[list[float]] | None:
    tf, agg, step = pick_timeframe(end - start)
    key = (pool, tf, agg, int(end // step))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    limit = min(1000, int((end - start) // step) + 2)
    try:
        data = await get_json(
            session,
            GECKO_URL.format(pool=pool, tf=tf),
            params={
                "aggregate": str(agg),
                "limit": str(max(limit, 30)),
                "currency": "usd",
                "token": "base",
                "before_timestamp": str(int(end)),
            },
            headers={"Accept": "application/json;version=20230302"},
            limiter=_gecko_limiter,
            retries=1,
        )
        rows = data["data"]["attributes"]["ohlcv_list"]
    except Exception as e:  # noqa: BLE001
        log.debug("GeckoTerminal sin velas para %s: %s", pool, e)
        return None
    candles = sorted([float(r[0]), *map(float, r[1:5])] for r in rows if r[0] >= start - step)
    _cache[key] = (time.time(), candles)
    return candles or None


async def build_chart(bot: Bot, mint: str) -> dict[str, Any] | None:
    if not BASE58.match(mint):
        return None
    st = bot.state
    pos = st.positions.get(mint) or next((p for p in reversed(st.closed) if p.mint == mint), None)
    if pos is None:
        return None
    now = time.time()
    end = (pos.close_time or now) + (300 if pos.closed else 0)
    start = pos.entry_time - 1800  # media hora antes de la compra para ver el contexto
    _, _, step = pick_timeframe(end - start)

    candles, source = None, "geckoterminal"
    if pos.pair_address and BASE58.match(pos.pair_address):
        candles = await gecko_candles(bot.session, pos.pair_address, start, min(end, now))
    if not candles:
        candles = candles_from_samples(st.load_prices(mint), step)
        source = "bot"

    trades = st.trades_for(mint) or [
        {
            "t": pos.entry_time,
            "side": "buy",
            "sol": pos.sol_spent,
            "price": pos.entry_price_usd,
            "reason": "",
        }
    ]
    ex = bot.cfg.exits
    stop_mult = 1.0 if pos.tp_done else 1 - ex.stop_loss_pct / 100
    return {
        "mint": mint,
        "symbol": pos.symbol,
        "source": source,
        "step": step,
        "candles": candles,
        "trades": trades,
        "entry_price": pos.entry_price_usd,
        "stop_price": None if pos.closed else pos.entry_price_usd * stop_mult,
        "tp_prices": []
        if pos.closed
        else [
            {"price": pos.entry_price_usd * m, "label": f"TP {m:g}x"}
            for i, (m, _) in enumerate(ex.take_profit_ladder)
            if i not in pos.tp_done
        ],
        "closed": pos.closed,
    }
