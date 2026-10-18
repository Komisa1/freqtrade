"""pump.fun en tiempo real via el websocket gratuito de PumpPortal.

Docs: https://pumpportal.fun/data-api/real-time
- subscribeNewToken: cada token recien creado en pump.fun
- subscribeMigration: tokens que completan la bonding curve y migran (los "graduados")
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from ..models import Candidate

log = logging.getLogger(__name__)

WS_URL = "wss://pumpportal.fun/api/data"


def parse_message(msg: dict[str, Any]) -> Candidate | None:
    mint = msg.get("mint")
    if not mint:
        return None
    tx_type = msg.get("txType")
    if tx_type == "create":
        source = "pumpfun-new"
    elif tx_type in ("migrate", "migration"):
        source = "pumpfun-graduated"
    else:
        return None
    return Candidate(
        mint=mint,
        source=source,
        symbol=msg.get("symbol", ""),
        name=msg.get("name", ""),
        extra={
            "dev": msg.get("traderPublicKey", ""),
            "dev_buy_sol": msg.get("solAmount", 0),
            "market_cap_sol": msg.get("marketCapSol", 0),
        },
    )


async def stream_pumpportal(
    session: aiohttp.ClientSession,
    queue: asyncio.Queue,
    *,
    new_tokens: bool = True,
    migrations: bool = True,
) -> None:
    backoff = 2.0
    while True:
        try:
            async with session.ws_connect(WS_URL, heartbeat=30) as ws:
                if new_tokens:
                    await ws.send_json({"method": "subscribeNewToken"})
                if migrations:
                    await ws.send_json({"method": "subscribeMigration"})
                log.info("Conectado a PumpPortal (pump.fun en vivo)")
                backoff = 2.0
                async for raw in ws:
                    if raw.type != aiohttp.WSMsgType.TEXT:
                        if raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    try:
                        msg = json.loads(raw.data)
                    except ValueError:
                        continue
                    cand = parse_message(msg) if isinstance(msg, dict) else None
                    if cand:
                        await queue.put(cand)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("PumpPortal desconectado (%s); reintentando en %.0fs", e, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)
