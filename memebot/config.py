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

    min_liquidity_usd: float = 15_000
    max_liquidity_usd: float = 2_000_000
    min_market_cap_usd: float = 30_000
    max_market_cap_usd: float = 3_000_000
    min_age_minutes: float = 3  # evita comprar en el primer bloque (snipers/bundles)
    max_age_minutes: float = 24 * 60
    min_volume_m5_usd: float = 2_000
    min_txns_m5: int = 25
    min_buy_sell_ratio_m5: float = 1.15  # mas compras que ventas en 5 min
    max_price_change_h1_pct: float = 100  # no perseguir monedas que ya volaron
    max_price_change_m5_pct: float = 25  # solo en modo "momentum": no comprar en plena vela
    min_price_change_m5_pct: float = -5
    min_score: float = 60  # puntaje minimo (0-100) del scoring
    # RugCheck
    rugcheck: bool = True
    rugcheck_max_score_normalised: float = 30  # 0 = limpio, cuanto mas alto, mas riesgo
    rugcheck_reject_levels: list[str] = field(default_factory=lambda: ["danger"])
    rugcheck_required: bool = True  # si RugCheck no responde, no comprar
    # Entrada. "pullback": cuando un token califica, Marin lo marca y espera a que retroceda
    # entre pullback_min_pct y pullback_max_pct desde su maximo (con compradores activos)
    # para comprar mas barato. "momentum": compra en cuanto califica (estrategia v1).
    entry_mode: str = "pullback"
    pullback_min_pct: float = 12
    pullback_max_pct: float = 35
    pullback_min_buy_sell_ratio: float = 0.9
    # v3: despues del retroceso, comprar solo cuando rebote este % desde el minimo
    # y vaya positiva en 5 min (0 = comprar en la caida, como la v2)
    pullback_bounce_pct: float = 4
    pullback_min_change_m5_pct: float = 0
    arm_minutes: float = 20  # si en este tiempo no retrocede, se descarta
    # Excepcion de pump (solo en modo pullback): una moneda que ya subio mas de
    # max_price_change_h1_pct puede entrar si pasa revisiones mas estrictas, y entonces
    # pide un retroceso mas profundo. Se mide aparte en el reporte ("Pump con excepcion").
    pump_exception: bool = True
    pump_max_change_h1_pct: float = 1000  # arriba de esto ni con excepcion
    pump_min_liquidity_usd: float = 40_000
    pump_min_market_cap_usd: float = 100_000
    pump_min_buy_sell_ratio: float = 1.3
    pump_min_txns_m5: int = 60
    pump_min_score: float = (
        60  # ojo: un pump no suma los puntos de "tendencia 1h", ya es mas estricto
    )
    pump_pullback_min_pct: float = 20
    pump_pullback_max_pct: float = 45
    # re-evaluacion de tokens que todavia no cumplen (p. ej. muy nuevos)
    watch_minutes: float = 30
    recheck_seconds: float = 20


@dataclass
class ExitConfig:
    stop_loss_pct: float = 22  # vender todo si cae 22% desde la entrada
    # escalera de toma de ganancias: [multiplo, % de la posicion ORIGINAL a vender]
    take_profit_ladder: list[list[float]] = field(
        default_factory=lambda: [[1.5, 40.0], [2.5, 30.0], [5.0, 15.0]]
    )
    # al llegar a este multiplo el stop sube al precio de entrada (0 = desactivado)
    breakeven_after_multiple: float = 1.25
    trailing_activate_multiple: float = 1.3  # el trailing se activa al llegar a 1.3x
    trailing_stop_pct: float = 18  # vender el resto si cae 18% desde el maximo
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
    # Freno por racha (solo en real): tras N perdidas seguidas, pausa (0 = desactivado)
    max_consecutive_losses: int = 4
    streak_pause_minutes: float = 60
    # Regimen del mercado (0-100): no comprar si esta por debajo (0 = solo medir, no filtrar)
    regime_min_score: float = 0


@dataclass
class SurvivalConfig:
    """Instinto de supervivencia (ver memebot/survival.py). Aplica en simulacion y en real."""

    enabled: bool = True
    life_capital_sol: float = 0  # capital de cada vida (0 = lo que tenga al nacer)
    death_drawdown_pct: float = 40  # muere si cae 40% desde su mejor momento
    fear_start_pct: float = 5  # empieza a tener miedo al caer 5% desde su mejor momento
    streak_fear_per_loss: float = 0.1  # cada perdida seguida suma 10% de miedo...
    streak_fear_max: float = 0.5  # ...hasta 50% (una ganancia lo borra)
    min_size_pct: float = 30  # con miedo maximo apuesta el 30% del tamano normal
    fear_score_bonus: float = 20  # con miedo maximo pide 20 puntos mas de puntaje
    fear_cooldown_mult: float = 4  # con miedo maximo descansa 4 veces mas tras perder
    fear_regime_from: float = 0.5  # desde 50% de miedo no compra si el mercado esta en contra
    fear_regime_min: float = 45
    sell_all_on_death: bool = True  # al morir vende todo lo que tenga abierto
    # tiene que seguir debajo de la linea de muerte este tiempo (un precio raro no la mata)
    death_confirm_seconds: float = 20
    min_trade_sol: float = 0.05  # con miedo nunca apuesta menos que esto (por las comisiones)


@dataclass
class ExecutionConfig:
    router: str = "jupiter"  # "jupiter" o "pumpportal"
    slippage_bps: int = 1500  # 15%: las meme coins se mueven rapido
    priority_fee_sol: float = 0.0005
    jupiter_base_url: str = "https://lite-api.jup.ag/swap/v1"
    confirm_timeout_seconds: float = 60
    price_poll_seconds: float = 2
    # costo simulado por operacion (slippage + fee del DEX) en modo simulacion. Pesimista a
    # proposito: si la estrategia gana asi, es mas creible en real (backtest-expert)
    paper_slippage_pct: float = 3


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
    survival: SurvivalConfig = field(default_factory=SurvivalConfig)
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
    "survival": SurvivalConfig,
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
    f = cfg.filters
    if f.entry_mode not in ("pullback", "momentum"):
        errors.append("filters.entry_mode debe ser 'pullback' o 'momentum'")
    if not 0 < f.pullback_min_pct < f.pullback_max_pct < 100:
        errors.append("filters: se necesita 0 < pullback_min_pct < pullback_max_pct < 100")
    if not 0 < f.pump_pullback_min_pct < f.pump_pullback_max_pct < 100:
        errors.append(
            "filters: se necesita 0 < pump_pullback_min_pct < pump_pullback_max_pct < 100"
        )
    sv = cfg.survival
    if not 0 <= sv.fear_start_pct < sv.death_drawdown_pct < 100:
        errors.append("survival: se necesita 0 <= fear_start_pct < death_drawdown_pct < 100")
    if not 0 < sv.min_size_pct <= 100:
        errors.append("survival.min_size_pct debe estar entre 0 y 100")
    if not 0 <= sv.streak_fear_max < 1 or sv.streak_fear_per_loss < 0:
        errors.append("survival: streak_fear_max debe estar entre 0 y 1 (sin llegar a 1)")
    if sv.death_confirm_seconds < 0 or sv.min_trade_sol < 0:
        errors.append("survival: death_confirm_seconds y min_trade_sol deben ser >= 0")
    if sv.fear_cooldown_mult < 1 or sv.fear_score_bonus < 0 or sv.life_capital_sol < 0:
        errors.append("survival: fear_cooldown_mult >= 1, fear_score_bonus y life_capital_sol >= 0")
    if cfg.live and not cfg.private_key:
        errors.append("Modo live requiere SOLANA_PRIVATE_KEY en el archivo .env")
    if errors:
        raise ValueError("Configuracion invalida:\n  - " + "\n  - ".join(errors))


def set_setting(config_path: Path, key: str, raw_value: str) -> tuple[Any, Any]:
    """Cambia un ajuste de config.json, p. ej. ("risk.max_open_positions", "4").
    Devuelve (valor_anterior, valor_nuevo). Si el valor no es valido, no cambia nada."""
    section, _, name = key.partition(".")
    if not name:
        section, name = "", section
    cls = _SECTIONS.get(section, Config) if section else Config
    if section and section not in _SECTIONS:
        raise ValueError(f"Seccion desconocida '{section}'. Opciones: {sorted(_SECTIONS)}")
    known = {f.name for f in fields(cls)} - {"private_key", "telegram_token"}
    if name not in known or name in _SECTIONS:
        raise ValueError(f"Ajuste desconocido '{key}'")
    try:
        value = json.loads(raw_value)
    except ValueError:
        value = raw_value
    original = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    data = json.loads(original) if original else {}
    target = data.setdefault(section, {}) if section else data
    default = getattr(cls(), name)
    old = target.get(name, default)
    target[name] = value
    config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        load_config(config_path, env_file=None)
    except Exception:
        if original is None:
            config_path.unlink()
        else:
            config_path.write_text(original, encoding="utf-8")
        raise
    return old, value


# Ajustes de estrategia que se pueden aplicar de un jalon con: python -m memebot estrategia
STRATEGY_PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "marin-v2": {
        "filters": {
            "min_liquidity_usd": 15_000,
            "min_market_cap_usd": 30_000,
            "max_price_change_h1_pct": 150,
            "max_price_change_m5_pct": 25,
            "entry_mode": "pullback",
            "pullback_min_pct": 12,
            "pullback_max_pct": 35,
            "pullback_min_buy_sell_ratio": 0.9,
            "arm_minutes": 20,
        },
        "exits": {"breakeven_after_multiple": 1.25},
    },
    # v3, basada en 85 operaciones simuladas: comprar solo tras confirmar el rebote,
    # no perseguir +100% en 1h, asegurar ganancia antes y stops mas rapidos
    "marin-v3": {
        "filters": {
            "min_liquidity_usd": 15_000,
            "min_market_cap_usd": 30_000,
            "max_price_change_h1_pct": 100,
            "max_price_change_m5_pct": 25,
            "entry_mode": "pullback",
            "pullback_min_pct": 12,
            "pullback_max_pct": 35,
            "pullback_min_buy_sell_ratio": 0.9,
            "pullback_bounce_pct": 4,
            "pullback_min_change_m5_pct": 0,
            "arm_minutes": 20,
        },
        "exits": {
            "stop_loss_pct": 22,
            "take_profit_ladder": [[1.5, 40.0], [2.5, 30.0], [5.0, 15.0]],
            "breakeven_after_multiple": 1.25,
            "trailing_activate_multiple": 1.3,
            "trailing_stop_pct": 18,
        },
        "execution": {"price_poll_seconds": 2, "paper_slippage_pct": 3},
    },
}


def preset_changes(config_path: Path, preset: str) -> list[tuple[str, Any, Any]]:
    """Lista de (ajuste, valor_actual, valor_nuevo) que cambiaria el preset."""
    current = load_config(config_path if config_path.exists() else None, env_file=None)
    out = []
    for section, values in STRATEGY_PRESETS[preset].items():
        for key, new in values.items():
            old = getattr(getattr(current, section), key)
            if old != new:
                out.append((f"{section}.{key}", old, new))
    return out


def apply_preset(config_path: Path, preset: str) -> None:
    original = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    data = json.loads(original) if original else {}
    for section, values in STRATEGY_PRESETS[preset].items():
        data.setdefault(section, {}).update(values)
    config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        load_config(config_path, env_file=None)
    except Exception:
        if original is None:
            config_path.unlink()
        else:
            config_path.write_text(original, encoding="utf-8")
        raise
