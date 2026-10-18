"""GMGN trending (EXPERIMENTAL).

GMGN no tiene una API publica oficial: estos endpoints son los que usa su web y suelen
estar detras de Cloudflare, asi que pueden devolver 403 o cambiar sin aviso. Si falla,
el bot sigue funcionando con las otras fuentes. Desactivado por defecto (sources.gmgn).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from ..http import get_json
from ..models import Candidate

log = logging.getLogger(__name__)

URL = "https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/{period}"
PARAMS = {
    "orderby": "swaps",
    "direction": "desc",
    "filters[]": "not_honeypot",
}
HEADERS = {"Referer": "https://gmgn.ai/", "Origin": "https://gmgn.ai"}


def parse_rank(data: Any) -> list[Candidate]:
    rank = ((data or {}).get("data") or {}).get("rank") or []
    out = []
    for item in rank:
        mint = item.get("address")
        if mint:
            out.append(
                Candidate(
                    mint=mint,
                    source="gmgn-trending",
                    symbol=item.get("symbol", ""),
                    extra={
                        "holders": item.get("holder_count"),
                        "smart_buys": item.get("smart_buy_24h"),
                    },
                )
            )
    return out


async def poll_gmgn(session: aiohttp.ClientSession, queue: asyncio.Queue, every: float) -> None:
    warned = False
    while True:
        for period in ("5m", "1h"):
            try:
                data = await get_json(
                    session, URL.format(period=period), params=PARAMS, headers=HEADERS, retries=1
                )
                for cand in parse_rank(data):
                    await queue.put(cand)
                warned = False
            except Exception as e:  # noqa: BLE001
                if not warned:
                    log.warning("GMGN no disponible (%s). Se reintentara en silencio.", e)
                    warned = True
        await asyncio.sleep(every)
