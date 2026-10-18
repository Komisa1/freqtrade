"""Estructuras de datos compartidas."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

WSOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass
class Candidate:
    """Un token detectado por alguna fuente, pendiente de evaluar."""

    mint: str
    source: str
    symbol: str = ""
    name: str = ""
    first_seen: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenSnapshot:
    """Foto del mercado de un token (normalmente su par mas liquido en DexScreener)."""

    mint: str
    symbol: str
    name: str
    pair_address: str
    dex_id: str
    price_usd: float
    liquidity_usd: float
    market_cap_usd: float
    volume_m5: float
    volume_h1: float
    buys_m5: int
    sells_m5: int
    buys_h1: int
    sells_h1: int
    change_m5: float
    change_h1: float
    pair_created_at: float  # epoch en segundos
    url: str = ""
    # DexScreener no siempre informa liquidez (p. ej. tokens en la bonding curve de pump.fun)
    liquidity_known: bool = True

    def age_minutes(self, now: float | None = None) -> float:
        if not self.pair_created_at:
            return 0.0
        return ((now or time.time()) - self.pair_created_at) / 60

    @property
    def txns_m5(self) -> int:
        return self.buys_m5 + self.sells_m5

    @property
    def buy_sell_ratio_m5(self) -> float:
        return self.buys_m5 / max(self.sells_m5, 1)


@dataclass
class Fill:
    """Resultado de una compra o venta ejecutada."""

    ok: bool
    sol_amount: float = 0.0  # SOL gastados (compra) o recibidos (venta)
    token_amount: float = 0.0  # unidades de token (raw en live, unidades virtuales en paper)
    signature: str = ""
    error: str = ""


@dataclass
class Position:
    mint: str
    symbol: str
    pair_address: str
    entry_price_usd: float
    entry_time: float
    sol_spent: float
    tokens_initial: float
    tokens_left: float
    peak_price_usd: float
    source: str = ""
    tp_done: list[int] = field(default_factory=list)
    sol_received: float = 0.0
    closed: bool = False
    close_time: float = 0.0
    close_reason: str = ""
    last_price_usd: float = 0.0
    signatures: list[str] = field(default_factory=list)

    def multiple(self, price: float) -> float:
        return price / self.entry_price_usd if self.entry_price_usd else 0.0

    @property
    def realized_pnl_sol(self) -> float:
        return self.sol_received - self.sol_spent

    def unrealized_value_sol(self, price: float) -> float:
        if not self.tokens_initial:
            return 0.0
        frac_left = self.tokens_left / self.tokens_initial
        return self.sol_spent * frac_left * self.multiple(price)
