"""Notificaciones: siempre al log, y a Telegram si esta configurado."""

from __future__ import annotations

import logging
import time
from collections import deque

import aiohttp

log = logging.getLogger("memebot")


class Notifier:
    def __init__(self, session: aiohttp.ClientSession, token: str, chat_id: str):
        self.session = session
        self.token = token
        self.chat_id = chat_id
        self.events: deque[dict] = deque(maxlen=200)  # historial para el panel web

    async def send(self, text: str) -> None:
        log.info(text)
        self.events.append({"t": time.time(), "text": text})
        if not (self.token and self.chat_id):
            return
        try:
            async with self.session.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    log.warning("Telegram respondio %s", resp.status)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo enviar a Telegram: %s", e)
