"""Cliente minimo de Solana: RPC JSON, firma de transacciones y consultas de saldo."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

import aiohttp
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

log = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000
TOKEN_PROGRAMS = (
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
)


def load_keypair(secret: str) -> Keypair:
    """Acepta base58 (export de Phantom/Solflare) o el array JSON de solana-keygen."""
    secret = secret.strip()
    if secret.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(secret)))
    return Keypair.from_base58_string(secret)


def sign_transaction(raw_tx: bytes, keypair: Keypair) -> bytes:
    tx = VersionedTransaction.from_bytes(raw_tx)
    signed = VersionedTransaction(tx.message, [keypair])
    return bytes(signed)


class SolanaRPC:
    def __init__(self, session: aiohttp.ClientSession, url: str):
        self.session = session
        self.url = url
        self._id = 0

    async def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        for attempt in range(3):
            try:
                async with self.session.post(
                    self.url, json=payload, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if resp.status == 429:
                        raise RuntimeError("RPC rate limit (429)")
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
                if attempt == 2:
                    raise RuntimeError(f"RPC {method} fallo: {e}") from e
                await asyncio.sleep(1 + attempt)
                continue
            if "error" in data:
                raise RuntimeError(f"RPC {method}: {data['error']}")
            return data.get("result")
        return None

    async def get_balance_sol(self, pubkey: str) -> float:
        res = await self.call("getBalance", [pubkey, {"commitment": "confirmed"}])
        return res["value"] / LAMPORTS_PER_SOL

    async def get_token_balance(self, owner: str, mint: str) -> tuple[int, int]:
        """Devuelve (cantidad_raw, decimales) del token en la wallet."""
        res = await self.call(
            "getTokenAccountsByOwner",
            [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )
        total, decimals = 0, 0
        for acc in (res or {}).get("value", []):
            info = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]
            total += int(info["amount"])
            decimals = int(info["decimals"])
        return total, decimals

    async def send_raw(self, signed_tx: bytes) -> str:
        return await self.call(
            "sendTransaction",
            [
                base64.b64encode(signed_tx).decode(),
                {"encoding": "base64", "skipPreflight": True, "maxRetries": 3},
            ],
        )

    async def confirm(self, signature: str, timeout: float) -> tuple[bool, str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            res = await self.call(
                "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]
            )
            status = ((res or {}).get("value") or [None])[0]
            if status:
                if status.get("err"):
                    return False, f"transaccion fallo: {status['err']}"
                if status.get("confirmationStatus") in ("confirmed", "finalized"):
                    return True, ""
            await asyncio.sleep(2)
        return False, "timeout esperando confirmacion"
