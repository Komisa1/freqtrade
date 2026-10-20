"""Ejecucion de ordenes.

- PaperTrader: simulacion. No toca la blockchain, aplica slippage y fees estimados.
- LiveTrader: compra/vende de verdad en Solana via Jupiter (agrega todos los DEX, incluida
  la bonding curve de pump.fun) o via la API local de PumpPortal. Firma localmente:
  tu clave privada nunca sale de tu computadora.
"""

from __future__ import annotations

import base64
import logging

import aiohttp

from .config import Config
from .http import get_json, post_json
from .models import WSOL_MINT, Fill, TokenSnapshot
from .solana import LAMPORTS_PER_SOL, SolanaRPC, load_keypair, sign_transaction

log = logging.getLogger(__name__)

PUMPPORTAL_TRADE_URL = "https://pumpportal.fun/api/trade-local"


class PaperTrader:
    """Simula fills al precio de DexScreener. Las 'unidades' de token son virtuales
    (SOL invertidos / precio USD), asi unidades * precio_actual = valor en SOL."""

    live = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.balance_sol = cfg.paper_starting_sol
        # costo estimado por operacion: slippage medio + fee de DEX
        self.cost_pct = cfg.execution.paper_slippage_pct / 100
        self.fee_sol = cfg.execution.priority_fee_sol + 0.000005

    async def wallet_sol(self) -> float:
        return self.balance_sol

    async def buy(self, mint: str, sol_amount: float, snap: TokenSnapshot) -> Fill:
        if sol_amount + self.fee_sol > self.balance_sol:
            return Fill(ok=False, error="saldo simulado insuficiente")
        self.balance_sol -= sol_amount + self.fee_sol
        units = sol_amount * (1 - self.cost_pct) / snap.price_usd
        return Fill(
            ok=True, sol_amount=sol_amount + self.fee_sol, token_amount=units, signature="paper"
        )

    async def sell(self, mint: str, token_amount: float, price_usd: float) -> Fill:
        gross = token_amount * price_usd
        received = max(0.0, gross * (1 - self.cost_pct) - self.fee_sol)
        self.balance_sol += received
        return Fill(ok=True, sol_amount=received, token_amount=token_amount, signature="paper")


class LiveTrader:
    live = True

    def __init__(self, cfg: Config, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.session = session
        self.rpc = SolanaRPC(session, cfg.rpc_url)
        self.keypair = load_keypair(cfg.private_key)
        self.pubkey = str(self.keypair.pubkey())

    async def wallet_sol(self) -> float:
        return await self.rpc.get_balance_sol(self.pubkey)

    # ---------- construccion de transacciones ----------

    async def _jupiter_tx(self, input_mint: str, output_mint: str, amount_raw: int) -> bytes:
        ex = self.cfg.execution
        quote = await get_json(
            self.session,
            f"{ex.jupiter_base_url}/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_raw),
                "slippageBps": str(ex.slippage_bps),
                "restrictIntermediateTokens": "true",
            },
            retries=2,
        )
        if not quote or "outAmount" not in quote:
            raise RuntimeError(f"Jupiter sin ruta: {str(quote)[:200]}")
        swap = await post_json(
            self.session,
            f"{ex.jupiter_base_url}/swap",
            {
                "quoteResponse": quote,
                "userPublicKey": self.pubkey,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "dynamicSlippage": False,
                "prioritizationFeeLamports": {
                    "priorityLevelWithMaxLamports": {
                        "maxLamports": int(ex.priority_fee_sol * LAMPORTS_PER_SOL),
                        "priorityLevel": "veryHigh",
                    }
                },
            },
        )
        return base64.b64decode(swap["swapTransaction"])

    async def _pumpportal_tx(self, action: str, mint: str, amount: float, in_sol: bool) -> bytes:
        ex = self.cfg.execution
        return await post_json(
            self.session,
            PUMPPORTAL_TRADE_URL,
            {
                "publicKey": self.pubkey,
                "action": action,
                "mint": mint,
                "amount": amount,
                "denominatedInSol": "true" if in_sol else "false",
                "slippage": ex.slippage_bps / 100,
                "priorityFee": ex.priority_fee_sol,
                "pool": "auto",
            },
            raw=True,
        )

    async def _execute(self, raw_tx: bytes) -> tuple[bool, str, str]:
        signed = sign_transaction(raw_tx, self.keypair)
        sig = await self.rpc.send_raw(signed)
        ok, err = await self.rpc.confirm(sig, self.cfg.execution.confirm_timeout_seconds)
        return ok, sig, err

    # ---------- operaciones ----------

    async def buy(self, mint: str, sol_amount: float, snap: TokenSnapshot) -> Fill:
        try:
            before_sol = await self.wallet_sol()
            before_tok, _ = await self.rpc.get_token_balance(self.pubkey, mint)
            if self.cfg.execution.router == "pumpportal":
                raw = await self._pumpportal_tx("buy", mint, sol_amount, in_sol=True)
            else:
                raw = await self._jupiter_tx(WSOL_MINT, mint, int(sol_amount * LAMPORTS_PER_SOL))
            ok, sig, err = await self._execute(raw)
            if not ok:
                return Fill(ok=False, signature=sig, error=err)
            after_tok, _ = await self.rpc.get_token_balance(self.pubkey, mint)
            after_sol = await self.wallet_sol()
            got = after_tok - before_tok
            if got <= 0:
                return Fill(
                    ok=False, signature=sig, error="confirmada pero no se recibieron tokens"
                )
            return Fill(ok=True, sol_amount=before_sol - after_sol, token_amount=got, signature=sig)
        except Exception as e:  # noqa: BLE001
            log.exception("Error comprando %s", mint)
            return Fill(ok=False, error=str(e))

    async def sell(self, mint: str, token_amount: float, price_usd: float) -> Fill:
        try:
            balance, decimals = await self.rpc.get_token_balance(self.pubkey, mint)
            amount = int(min(token_amount, balance))
            if amount <= 0:
                return Fill(ok=False, error="no hay tokens en la wallet")
            before_sol = await self.wallet_sol()
            if self.cfg.execution.router == "pumpportal":
                ui = "100%" if amount >= balance else amount / 10**decimals
                raw = await self._pumpportal_tx("sell", mint, ui, in_sol=False)
            else:
                raw = await self._jupiter_tx(mint, WSOL_MINT, amount)
            ok, sig, err = await self._execute(raw)
            if not ok:
                return Fill(ok=False, signature=sig, error=err)
            after_sol = await self.wallet_sol()
            return Fill(
                ok=True,
                sol_amount=max(0.0, after_sol - before_sol),
                token_amount=amount,
                signature=sig,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Error vendiendo %s", mint)
            return Fill(ok=False, error=str(e))
