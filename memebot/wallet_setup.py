"""Preparacion para operar con dinero real, sin arriesgar nada todavia.

- setup_wallet: crea la wallet del bot y la guarda en .env (sin copiar claves a mano).
- preflight: revisa wallet, RPC, Jupiter y riesgo. NO envia ninguna transaccion.
"""

from __future__ import annotations

from pathlib import Path

import aiohttp
from solders.keypair import Keypair

from .config import Config
from .http import get_json
from .models import WSOL_MINT
from .solana import LAMPORTS_PER_SOL, SolanaRPC, load_keypair

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PUBLIC_RPC = "api.mainnet-beta.solana.com"
KEY_VAR = "SOLANA_PRIVATE_KEY"


def read_env_key(env_path: Path) -> str:
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(KEY_VAR + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def setup_wallet(env_path: Path) -> tuple[str, bool]:
    """Devuelve (direccion, creada_ahora). Si ya hay una clave en .env, no la toca."""
    existing = read_env_key(env_path)
    if existing:
        return str(load_keypair(existing).pubkey()), False

    kp = Keypair()
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith(KEY_VAR + "="):
            lines[i] = f"{KEY_VAR}={kp}"
            break
    else:
        lines.append(f"{KEY_VAR}={kp}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(kp.pubkey()), True


def import_wallet(env_path: Path, secret: str) -> str:
    """Guarda una clave existente (p. ej. la de tu wallet de GMGN/Phantom) en .env."""
    address = str(load_keypair(secret).pubkey())  # valida antes de escribir nada
    secret = secret.strip()
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith(KEY_VAR + "="):
            lines[i] = f"{KEY_VAR}={secret}"
            break
    else:
        lines.append(f"{KEY_VAR}={secret}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return address


MIN_TRADE_SOL = 0.05  # por debajo de esto las comisiones se comen buena parte de la ganancia
TRADE_FEES_SOL = 0.003  # aprox. por operacion completa: prioridad x2 + cuenta de token


def recommended_limits(balance_sol: float, reserve: float = 0.05) -> dict[str, float | int]:
    """Limites prudentes para una wallet chica: ~10% por operacion y como maximo la mitad
    del saldo en juego. Si el saldo es muy chico, prefiere menos operaciones pero no tan
    diminutas que las comisiones se coman la ganancia."""
    usable = max(0.0, balance_sol - reserve)
    trade = round(max(MIN_TRADE_SOL, balance_sol * 0.1), 3)
    capital = round(min(usable, max(trade, balance_sol * 0.5)), 3)
    positions = max(1, min(3, int(capital // trade))) if trade else 1
    return {
        "trade_size_sol": trade,
        "max_open_positions": positions,
        "max_capital_sol": round(trade * positions, 3),
        "max_daily_loss_sol": round(max(trade, balance_sol * 0.2), 3),
        "max_total_loss_sol": round(max(trade * 2, balance_sol * 0.35), 3),
        "min_sol_reserve": reserve,
    }


def apply_limits(config_path: Path, limits: dict[str, float | int]) -> None:
    """Escribe los limites en config.json conservando el resto de ajustes."""
    import json

    data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    data.setdefault("risk", {}).update(limits)
    config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def risk_warnings(balance_sol: float, cfg: Config) -> list[str]:
    r = cfg.risk
    out = []
    tip = "Corre: python -m memebot safe-limits"
    needed = r.trade_size_sol * r.max_open_positions + r.min_sol_reserve
    if balance_sol < r.trade_size_sol + r.min_sol_reserve:
        out.append(
            f"Saldo {balance_sol:.4f} SOL no alcanza ni para una operacion "
            f"({r.trade_size_sol} SOL + {r.min_sol_reserve} de reserva)."
        )
    elif balance_sol < needed:
        fits = int((balance_sol - r.min_sol_reserve) // r.trade_size_sol)
        out.append(
            f"Con {balance_sol:.4f} SOL solo caben {fits} de {r.max_open_positions} "
            f"posiciones a la vez. {tip}"
        )
    if balance_sol > 0 and r.trade_size_sol > balance_sol * 0.2:
        out.append(f"Cada operacion ({r.trade_size_sol} SOL) es mas del 20% de tu saldo. {tip}")
    if r.trade_size_sol < MIN_TRADE_SOL:
        pct = 100 * TRADE_FEES_SOL / r.trade_size_sol
        out.append(
            f"Operaciones de {r.trade_size_sol} SOL: las comisiones (~{TRADE_FEES_SOL} SOL) "
            f"serian ~{pct:.0f}% de cada operacion."
        )
    if balance_sol > 0 and r.max_daily_loss_sol > balance_sol * 0.3:
        out.append(
            f"Perdida diaria maxima ({r.max_daily_loss_sol} SOL) es mas del 30% del saldo. {tip}"
        )
    return out


async def preflight(cfg: Config) -> list[tuple[str, str]]:
    """Lista de (estado, mensaje) con estado en OK / AVISO / FALTA."""
    checks: list[tuple[str, str]] = []
    if not cfg.private_key:
        checks.append(("FALTA", "No hay wallet del bot. Corre: python -m memebot setup-wallet"))
        return checks
    try:
        kp = load_keypair(cfg.private_key)
    except Exception as e:  # noqa: BLE001
        checks.append(("FALTA", f"La clave de .env no es valida: {e}"))
        return checks
    pubkey = str(kp.pubkey())
    checks.append(("OK", f"Wallet del bot: {pubkey}"))
    checks.append(("OK", f"Verla en GMGN: https://gmgn.ai/sol/address/{pubkey}"))

    if PUBLIC_RPC in cfg.rpc_url:
        checks.append(
            (
                "AVISO",
                "Usas el RPC publico de Solana (lento). Para real conviene uno gratis de "
                "Helius: https://helius.dev -> SOLANA_RPC_URL en .env",
            )
        )

    async with aiohttp.ClientSession(trust_env=True) as session:
        balance = None
        try:
            balance = await SolanaRPC(session, cfg.rpc_url).get_balance_sol(pubkey)
            checks.append(("OK", f"Conexion a Solana OK. Saldo: {balance:.4f} SOL"))
        except Exception as e:  # noqa: BLE001
            checks.append(("FALTA", f"No se pudo conectar al RPC de Solana: {e}"))

        try:
            quote = await get_json(
                session,
                f"{cfg.execution.jupiter_base_url}/quote",
                params={
                    "inputMint": WSOL_MINT,
                    "outputMint": USDC_MINT,
                    "amount": str(int(cfg.risk.trade_size_sol * LAMPORTS_PER_SOL)),
                    "slippageBps": "50",
                },
                retries=2,
            )
            usdc = int(quote["outAmount"]) / 1e6
            checks.append(
                (
                    "OK",
                    f"Jupiter OK: {cfg.risk.trade_size_sol} SOL ~ ${usdc:,.2f} (solo cotizacion)",
                )
            )
        except Exception as e:  # noqa: BLE001
            checks.append(("FALTA", f"No se pudo cotizar en Jupiter: {e}"))

    if balance is not None:
        if balance == 0:
            checks.append(
                ("AVISO", "La wallet esta vacia. Deposita SOL solo cuando decidas ir a real.")
            )
        else:
            for w in risk_warnings(balance, cfg):
                checks.append(("AVISO", w))
    r = cfg.risk
    if r.max_capital_sol <= 0:
        checks.append(
            (
                "AVISO",
                "No hay tope de capital: el bot puede usar todo el saldo de la wallet. Si es tu "
                "wallet personal, pon risk.max_capital_sol (ej. 0.2) en config.json",
            )
        )
    else:
        checks.append(("OK", f"Tope de capital del bot: {r.max_capital_sol} SOL"))
    if r.max_total_loss_sol > 0:
        checks.append(("OK", f"Tope de perdida total: {r.max_total_loss_sol} SOL"))
    checks.append(
        (
            "OK",
            f"Riesgo actual: {r.trade_size_sol} SOL por operacion, max {r.max_open_positions} "
            f"posiciones, perdida diaria max {r.max_daily_loss_sol} SOL, "
            f"stop loss -{cfg.exits.stop_loss_pct:.0f}%",
        )
    )
    return checks
