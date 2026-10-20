import time

from memebot.models import Position, TokenSnapshot


def snap(**kw) -> TokenSnapshot:
    base = dict(
        mint="MINT1",
        symbol="PEPE",
        name="Pepe",
        pair_address="PAIR1",
        dex_id="raydium",
        price_usd=0.001,
        liquidity_usd=40_000,
        market_cap_usd=200_000,
        volume_m5=15_000,
        volume_h1=120_000,
        buys_m5=180,
        sells_m5=90,
        buys_h1=1500,
        sells_h1=900,
        change_m5=12,
        change_h1=80,
        pair_created_at=time.time() - 30 * 60,
    )
    base.update(kw)
    return TokenSnapshot(**base)


def position(**kw) -> Position:
    now = time.time()
    base = dict(
        mint="MINT1",
        symbol="PEPE",
        pair_address="PAIR1",
        entry_price_usd=1.0,
        entry_time=now - 60,
        sol_spent=0.1,
        tokens_initial=1000.0,
        tokens_left=1000.0,
        peak_price_usd=1.0,
    )
    base.update(kw)
    return Position(**base)


# Parametros de la v2, para que los tests de mecanica no dependan de los valores por defecto
def v2_exits():
    from memebot.config import ExitConfig

    return ExitConfig(
        stop_loss_pct=30,
        take_profit_ladder=[[2.0, 50.0], [4.0, 25.0]],
        trailing_activate_multiple=1.5,
        trailing_stop_pct=25,
    )


def v2_filters(**kw):
    from memebot.config import FilterConfig

    return FilterConfig(pullback_bounce_pct=0, pullback_min_change_m5_pct=-100, **kw)


def v2_config(tmp_path, **kw):
    from memebot.config import Config

    return Config(data_dir=str(tmp_path), exits=v2_exits(), filters=v2_filters(), **kw)
