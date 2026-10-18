"""DexScreener: datos de mercado de cualquier token de Solana + tokens promocionados recientes.

API publica y gratuita: https://docs.dexscreener.com/api/reference
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

import aiohttp

from ..http import RateLimiter, get_json
from ..models import Candidate, TokenSnapshot

log = logging.getLogger(__name__)

BASE = "https://api.dexscreener.com"
CHAIN = "solana"
BATCH = 30  # maximo de tokens por llamada a /tokens/v1

# /tokens/v1 permite 300 req/min; los endpoints de perfiles/boosts 60 req/min.
_pairs_limiter = RateLimiter(240)
_lists_limiter = RateLimiter(50)


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_pair(pair: dict[str, Any]) -> TokenSnapshot:
    base = pair.get("baseToken") or {}
    txns = pair.get("txns") or {}
    vol = pair.get("volume") or {}
    chg = pair.get("priceChange") or {}
    liq = pair.get("liquidity") or {}
    created = pair.get("pairCreatedAt") or 0
    return TokenSnapshot(
        mint=base.get("address", ""),
        symbol=base.get("symbol", ""),
        name=base.get("name", ""),
        pair_address=pair.get("pairAddress", ""),
        dex_id=pair.get("dexId", ""),
        price_usd=_f(pair.get("priceUsd")),
        liquidity_usd=_f(liq.get("usd")),
        market_cap_usd=_f(pair.get("marketCap") or pair.get("fdv")),
        volume_m5=_f(vol.get("m5")),
        volume_h1=_f(vol.get("h1")),
        buys_m5=int((txns.get("m5") or {}).get("buys", 0)),
        sells_m5=int((txns.get("m5") or {}).get("sells", 0)),
        buys_h1=int((txns.get("h1") or {}).get("buys", 0)),
        sells_h1=int((txns.get("h1") or {}).get("sells", 0)),
        change_m5=_f(chg.get("m5")),
        change_h1=_f(chg.get("h1")),
        pair_created_at=created / 1000 if created > 1e11 else created,
        url=pair.get("url", ""),
        liquidity_known=liq.get("usd") is not None,
    )


def best_pairs(pairs: Iterable[dict[str, Any]]) -> dict[str, TokenSnapshot]:
    """Para cada token se queda con su par mas liquido (donde el token es la base)."""
    out: dict[str, TokenSnapshot] = {}
    for pair in pairs:
        if pair.get("chainId") != CHAIN:
            continue
        snap = parse_pair(pair)
        if not snap.mint or snap.price_usd <= 0:
            continue
        cur = out.get(snap.mint)
        if cur is None or snap.liquidity_usd > cur.liquidity_usd:
            out[snap.mint] = snap
    return out


class DexScreener:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def snapshots(self, mints: Iterable[str]) -> dict[str, TokenSnapshot]:
        mints = list(dict.fromkeys(mints))
        chunks = [mints[i : i + BATCH] for i in range(0, len(mints), BATCH)]
        results = await asyncio.gather(
            *(self._fetch_chunk(c) for c in chunks), return_exceptions=True
        )
        out: dict[str, TokenSnapshot] = {}
        for res in results:
            if isinstance(res, Exception):
                log.warning("DexScreener: %s", res)
                continue
            out.update(res)
        return out

    async def _fetch_chunk(self, mints: list[str]) -> dict[str, TokenSnapshot]:
        url = f"{BASE}/tokens/v1/{CHAIN}/{','.join(mints)}"
        data = await get_json(self.session, url, limiter=_pairs_limiter)
        pairs = data if isinstance(data, list) else (data or {}).get("pairs") or []
        return best_pairs(pairs)

    async def snapshot(self, mint: str) -> TokenSnapshot | None:
        return (await self.snapshots([mint])).get(mint)

    async def discover(self) -> list[Candidate]:
        """Tokens de Solana con perfil o boost reciente (suelen ser los que tienen 'atencion')."""
        out: dict[str, Candidate] = {}
        for path, tag in (
            ("/token-boosts/latest/v1", "dexscreener-boost"),
            ("/token-boosts/top/v1", "dexscreener-topboost"),
            ("/token-profiles/latest/v1", "dexscreener-profile"),
        ):
            try:
                data = await get_json(self.session, BASE + path, limiter=_lists_limiter)
            except RuntimeError as e:
                log.warning("DexScreener %s: %s", path, e)
                continue
            for item in data if isinstance(data, list) else []:
                if item.get("chainId") == CHAIN and item.get("tokenAddress"):
                    mint = item["tokenAddress"]
                    out.setdefault(mint, Candidate(mint=mint, source=tag))
        return list(out.values())


async def poll_dexscreener(dex: DexScreener, queue: asyncio.Queue, every: float) -> None:
    while True:
        try:
            for cand in await dex.discover():
                await queue.put(cand)
        except Exception:  # noqa: BLE001 - una fuente caida no debe tumbar el bot
            log.exception("Error en fuente DexScreener")
        await asyncio.sleep(every)
