"""Helpers HTTP con reintentos y limite de velocidad simple."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}


class RateLimiter:
    """Permite como maximo `per_minute` llamadas por minuto."""

    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._next > now:
                await asyncio.sleep(self._next - now)
            self._next = max(now, self._next) + self.interval


async def get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    limiter: RateLimiter | None = None,
    retries: int = 3,
    timeout: float = 15,
) -> Any:
    last_err: Exception | None = None
    for attempt in range(retries):
        if limiter:
            await limiter.wait()
        try:
            async with session.get(
                url,
                params=params,
                headers={**DEFAULT_HEADERS, **(headers or {})},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 429 or resp.status >= 500:
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=resp.status, message=resp.reason
                    )
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_err = e
            status = getattr(e, "status", None)
            if status and 400 <= status < 500 and status != 429:
                break  # error del cliente: no tiene sentido reintentar
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} fallo: {last_err}")


async def post_json(
    session: aiohttp.ClientSession,
    url: str,
    payload: Any,
    *,
    timeout: float = 20,
    raw: bool = False,
) -> Any:
    async with session.post(
        url,
        json=payload,
        headers={**DEFAULT_HEADERS, "Content-Type": "application/json"},
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status >= 400:
            body = await resp.text()
            raise RuntimeError(f"POST {url} -> HTTP {resp.status}: {body[:300]}")
        if raw:
            return await resp.read()
        return await resp.json(content_type=None)
