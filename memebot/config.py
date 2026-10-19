"""Carga de configuracion: config.json (parametros) + .env (secretos)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class SourcesConfig:
    pumpportal: bool = True  # tokens nuevos de pump.fun (websocket de PumpPortal)
    pumpportal_migrations: bool = True  # tokens que se "graduan" de pump.fun a PumpSwap/Raydium
    dexscreener: bool = True  # boosts/perfiles recientes de DexScreener
    gmgn: bool = False  # trending de GMGN (experimental: API no oficial, a menudo bloqueada)
    dexscreener_poll_seconds: int = 30
    gmgn_poll_seconds: int = 60


@dataclass
class FilterConfig:
    """Filtros duros: si un token no los cumple, no se compra."""

    min_liquidity_usd: float = 8_000
    max_liquidity_usd: float = 2_000_000
    min_market_cap_usd: float = 15_000
    max_market_cap_usd: float = 3_000_000
    min_age_minutes: float = 3  # evita comprar en el primer bloque (snipers/bundles)
    max_age_minutes: float = 24 * 60
    min_volume_m5_usd: float = 2_000
    min_txns_m5: int = 25
    min_buy_sell_ratio_m5: float = 1.15  # mas compras que ventas en 5 min
    max_price_change_h1_pct: float = 400  # no perseguir velas verticales
    min_price_change_m5_pct: float = -5
    min_score: float = 60  # puntaje minimo (0-100) del scoring
    # RugCheck
    rugcheck: bool = True
    rugcheck_max_score_normalised: float = 30  # 0 = limpio, cuanto mas alto, mas riesgo
    rugcheck_reject_levels: list[str] = field(default_factory=lambda: ["danger"])
    rugcheck_required: bool = True  # si RugCheck no responde, no comprar
    # re-evaluacion de tokens que todavia no cumplen (p. ej. muy nuevos)
    watch_minutes: float = 30
    recheck_seconds: float = 20


@dataclass
class ExitConfig:
    stop_loss_pct: float = 30  # vender todo si cae 30% desde la entrada
    # escalera de toma de ganancias: [multiplo, % de la posicion ORIGINAL a vender]
    take_profit_ladder: list[list[float]] = field(
        default_factory=lambda: [[2.0, 50.0], [4.0, 25.0]]
    )
    trailing_activate_multiple: float = 1.5  # el trailing se activa al llegar a 1.5x
    trailing_stop_pct: float = 25  # vender el resto si cae 25% desde el maximo
    max_hold_minutes: float = 240
    stale_minutes: float = 45  # si en 45 min no hizo +10%, salir
    stale_min_gain_pct: float = 10
    min_liquidity_usd_exit: float = 3_000  # liquidez cayendo = posible rug -> salir


@dataclass
class RiskConfig:
    trade_size_sol: float = 0.1
    max_open_positions: int = 3
    max_daily_loss_sol: float = 0.5  # al llegar a esta perdida diaria, deja de comprar
    min_sol_reserve: float = 0.05  # SOL que nunca se gasta (fees, cuentas de token)
    cooldown_minutes_after_loss: float = 10
    never_rebuy_same_token: bool = True
    # Topes para cuando el bot usa tu wallet personal (0 = sin tope):
    max_capital_sol: float = 0  # SOL maximos metidos en posiciones abiertas a la vez
    max_total_loss_sol: float = 0  # si la perdida acumulada llega a esto, deja de comprar


@dataclass
class ExecutionConfig:
    router: str = "jupiter"  # "jupiter" o "pumpportal"
    slippage_bps: int = 1500  # 15%: las meme coins se mueven rapido
    priority_fee_sol: float = 0.0005
    jupiter_base_url: str = "https://lite-api.jup.ag/swap/v1"
    confirm_timeout_seconds: float = 60
    price_poll_seconds: float = 5


@dataclass
class DashboardConfig:
    enabled: bool = True  # panel web en http://127.0.0.1:<port>
    port: int = 8080
    open_browser: bool = True  # abrir el navegador al arrancar


@dataclass
class Config:
    live: bool = False  # False = modo simulacion (paper trading), no se gasta dinero real
    paper_starting_sol: float = 2.0
    data_dir: str = "data"
    log_level: str = "INFO"
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    exits: ExitConfig = field(default_factory=ExitConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    # secretos (vienen del entorno / .env, nunca de config.json)
    private_key: str = field(default="", repr=False)
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    telegram_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for secret in ("private_key", "telegram_token"):
            d.pop(secret, None)
        return d


_SECTIONS = {
    "sources": SourcesConfig,
    "filters": FilterConfig,
    "exits": ExitConfig,
    "risk": RiskConfig,
    "execution": ExecutionConfig,
    "dashboard": DashboardConfig,
}


def _build(cls, data: dict[str, Any], path: str):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Claves desconocidas en '{path}': {sorted(unknown)}")
    return cls(**data)


def load_dotenv(path: Path) -> None:
    """Lector minimo de .env (KEY=VALUE). No pisa variables ya definidas."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: str | Path | None = None, env_file: str | Path | None = ".env") -> Config:
    if env_file:
        load_dotenv(Path(env_file))

    raw: dict[str, Any] = {}
    if path and Path(path).exists():
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    elif path:
        raise FileNotFoundError(f"No existe el archivo de configuracion: {path}")

    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key.startswith("_"):  # permite claves de comentario como "_nota"
            continue
        if key in _SECTIONS:
            kwargs[key] = _build(_SECTIONS[key], value, key)
        elif key in ("private_key", "telegram_token"):
            raise ValueError(f"'{key}' va en el archivo .env, no en config.json")
        else:
            kwargs[key] = value
    cfg = _build(Config, kwargs, "config")

    cfg.private_key = os.environ.get("SOLANA_PRIVATE_KEY", "")
    cfg.rpc_url = os.environ.get("SOLANA_RPC_URL", cfg.rpc_url) or cfg.rpc_url
    cfg.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cfg.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    errors = []
    if cfg.execution.router not in ("jupiter", "pumpportal"):
        errors.append("execution.router debe ser 'jupiter' o 'pumpportal'")
    if cfg.risk.trade_size_sol <= 0:
        errors.append("risk.trade_size_sol debe ser > 0")
    if cfg.risk.max_open_positions < 1:
        errors.append("risk.max_open_positions debe ser >= 1")
    if not 0 < cfg.exits.stop_loss_pct < 100:
        errors.append("exits.stop_loss_pct debe estar entre 0 y 100")
    total = 0.0
    for step in cfg.exits.take_profit_ladder:
        if len(step) != 2 or step[0] <= 1 or not 0 < step[1] <= 100:
            errors.append(f"take_profit_ladder: paso invalido {step} (usa [multiplo>1, %])")
        total += step[1] if len(step) == 2 else 0
    if total > 100:
        errors.append("take_profit_ladder: la suma de porcentajes no puede superar 100")
    if cfg.live and not cfg.private_key:
        errors.append("Modo live requiere SOLANA_PRIVATE_KEY en el archivo .env")
    if errors:
        raise ValueError("Configuracion invalida:\n  - " + "\n  - ".join(errors))
