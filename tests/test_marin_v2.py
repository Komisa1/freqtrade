"""Estrategia Marin v2: entrada en retroceso, breakeven temprano y analisis de resultados."""

import json
import time

import aiohttp
from helpers import position, snap, v2_config, v2_exits, v2_filters

from memebot.analysis import compare_winners_losers, mode_stats
from memebot.bot import Bot
from memebot.config import (
    Config,
    ExitConfig,
    FilterConfig,
    apply_preset,
    load_config,
    preset_changes,
)
from memebot.entry import Armed, check_pullback
from memebot.exits import decide_exit
from memebot.models import Candidate
from memebot.safety import SafetyResult
from memebot.scoring import Verdict, evaluate

NOW = time.time()


def test_check_pullback_states():
    cfg = v2_filters()
    a = Armed("M", "X", high=1.0, armed_at=NOW, score=70)
    assert check_pullback(a, snap(price_usd=1.2), cfg, NOW) == "wait"  # nuevo maximo
    assert a.high == 1.2
    assert check_pullback(a, snap(price_usd=1.15), cfg, NOW) == "wait"  # solo -4%
    assert "esperando" in a.status
    assert check_pullback(a, snap(price_usd=1.0), cfg, NOW) == "buy"  # -16.7%
    assert 16 < a.last_dd < 17
    weak = snap(price_usd=1.0, buys_m5=40, sells_m5=100)
    assert check_pullback(a, weak, cfg, NOW) == "wait"  # cae sin compradores
    assert check_pullback(a, snap(price_usd=0.7), cfg, NOW) == "drop"  # -42%
    old = Armed("M", "X", high=1.0, armed_at=NOW - 3600, score=70)
    assert check_pullback(old, snap(price_usd=0.85), cfg, NOW) == "drop"  # expiro


def test_momentum_mode_does_not_chase_fast_candles():
    s = snap(change_m5=40)
    assert evaluate(s, FilterConfig(entry_mode="momentum")).verdict is Verdict.WAIT
    assert evaluate(s, FilterConfig(entry_mode="pullback")).verdict is Verdict.BUY
    no_exc = FilterConfig(pump_exception=False)
    assert evaluate(snap(change_h1=200), no_exc).verdict is Verdict.WAIT  # max 150%


def test_pump_exception():
    cfg = FilterConfig()
    healthy = snap(change_h1=300, liquidity_usd=60_000, market_cap_usd=400_000)
    ev = evaluate(healthy, cfg)
    assert ev.verdict is Verdict.BUY and ev.pump
    assert any("excepcion de pump" in r for r in ev.reasons)
    # pump con poca liquidez, pocos compradores o demasiado vertical: no pasa
    assert evaluate(snap(change_h1=300, liquidity_usd=20_000), cfg).verdict is Verdict.WAIT
    weak = snap(change_h1=300, liquidity_usd=60_000, buys_m5=100, sells_m5=90)
    assert evaluate(weak, cfg).verdict is Verdict.WAIT
    assert evaluate(snap(change_h1=1500, liquidity_usd=60_000), cfg).verdict is Verdict.WAIT
    assert not evaluate(snap(change_h1=80), cfg).pump  # una normal no es pump
    # en modo momentum no hay excepcion
    mom = FilterConfig(entry_mode="momentum")
    assert (
        evaluate(snap(change_h1=300, liquidity_usd=60_000, change_m5=10), mom).verdict
        is Verdict.WAIT
    )


def test_pump_needs_deeper_pullback():
    cfg = FilterConfig()
    a = Armed("M", "X", high=1.0, armed_at=NOW, score=70, pump=True)
    assert check_pullback(a, snap(price_usd=0.85), cfg, NOW) == "wait"  # -15% no basta
    assert check_pullback(a, snap(price_usd=0.75), cfg, NOW) == "buy"  # -25%
    b = Armed("M", "X", high=1.0, armed_at=NOW, score=70, pump=True)
    assert check_pullback(b, snap(price_usd=0.6), cfg, NOW) == "buy"  # -40% ok para un pump
    assert check_pullback(b, snap(price_usd=0.5), cfg, NOW) == "drop"  # -50% se desplomo


def test_breakeven_after_25pct():
    cfg = v2_exits()
    pos = position(peak_price_usd=1.3)  # llego a 1.3x
    a = decide_exit(pos, 0.99, 50_000, NOW, cfg)
    assert a and "breakeven" in a.reason
    assert decide_exit(position(peak_price_usd=1.2), 0.99, 50_000, NOW, cfg) is None
    off = v2_exits()
    off.breakeven_after_multiple = 0
    assert decide_exit(position(peak_price_usd=1.3), 0.99, 50_000, NOW, off) is None


async def test_bot_arms_waits_and_buys_on_pullback(tmp_path, monkeypatch):
    async with aiohttp.ClientSession() as session:
        bot = Bot(v2_config(tmp_path), session)

        async def ok(mint):
            return SafetyResult(ok=True)

        monkeypatch.setattr(bot.rugcheck, "check", ok)
        prices = iter([1.0, 1.3, 1.25, 1.1])

        async def snaps(mints):
            return {"MINT1": snap(price_usd=next(prices))}

        monkeypatch.setattr(bot.dex, "snapshots", snaps)
        bot.watchlist["MINT1"] = Candidate(mint="MINT1", source="test", symbol="PEPE")

        await bot.evaluate_once(NOW)  # califica -> en la mira, no compra
        assert "MINT1" in bot.armed and not bot.state.positions
        assert bot.radar["MINT1"]["armed"]["dd"] == 0
        await bot.evaluate_once(NOW + 20)  # sube a 1.3 (nuevo maximo)
        await bot.evaluate_once(NOW + 40)  # 1.25: -4%, todavia no
        assert not bot.state.positions
        await bot.evaluate_once(NOW + 60)  # 1.1: -15% -> compra
        pos = bot.state.positions["MINT1"]
        assert pos.entry_price_usd == 1.1
        assert pos.entry_snapshot["mode"] == "pullback"
        assert 15 < pos.entry_snapshot["pullback_pct"] < 16
        assert "MINT1" not in bot.armed


async def test_bot_drops_token_that_dumps(tmp_path, monkeypatch):
    async with aiohttp.ClientSession() as session:
        bot = Bot(Config(data_dir=str(tmp_path)), session)
        prices = iter([1.0, 0.6])

        async def snaps(mints):
            return {"MINT1": snap(price_usd=next(prices))}

        monkeypatch.setattr(bot.dex, "snapshots", snaps)
        bot.watchlist["MINT1"] = Candidate(mint="MINT1", source="test")
        await bot.evaluate_once(NOW)
        await bot.evaluate_once(NOW + 20)
        assert "MINT1" not in bot.armed and not bot.state.positions


async def test_momentum_mode_buys_immediately(tmp_path, monkeypatch):
    cfg = Config(data_dir=str(tmp_path), filters=FilterConfig(entry_mode="momentum"))
    async with aiohttp.ClientSession() as session:
        bot = Bot(cfg, session)

        async def ok(mint):
            return SafetyResult(ok=True)

        async def snaps(mints):
            return {"MINT1": snap()}

        monkeypatch.setattr(bot.rugcheck, "check", ok)
        monkeypatch.setattr(bot.dex, "snapshots", snaps)
        bot.watchlist["MINT1"] = Candidate(mint="MINT1", source="test")
        await bot.evaluate_once(NOW)
        assert bot.state.positions["MINT1"].entry_snapshot["mode"] == "momentum"


def test_strategy_preset(tmp_path):
    p = tmp_path / "config.json"
    old = {"filters": {"min_liquidity_usd": 8000, "max_price_change_h1_pct": 400}, "risk": {"x": 1}}
    old["risk"] = {"trade_size_sol": 0.05}
    p.write_text(json.dumps(old))
    changes = dict((k, (a, b)) for k, a, b in preset_changes(p, "marin-v2"))
    assert changes["filters.min_liquidity_usd"] == (8000, 15_000)
    assert changes["filters.max_price_change_h1_pct"] == (400, 150)
    apply_preset(p, "marin-v2")
    cfg = load_config(p, env_file=None)
    assert cfg.filters.entry_mode == "pullback" and cfg.risk.trade_size_sol == 0.05
    assert preset_changes(p, "marin-v2") == []


def test_compare_winners_losers():
    w = position(mint="w", sol_received=0.2, entry_snapshot={"mode": "pullback", "chg_h1": 40})
    lo = position(mint="l", sol_received=0.05, entry_snapshot={"mode": "pullback", "chg_h1": 220})
    v1 = position(mint="v", sol_received=0.05)
    c = compare_winners_losers([w, lo, v1])
    assert c["wins"] == 1 and c["losses"] == 1
    row = next(r for r in c["rows"] if r["key"] == "chg_h1")
    assert row["win"] == 40 and row["loss"] == 220
    labels = {s["key"]: s for s in mode_stats([w, lo, v1])}
    assert labels["pullback"]["trades"] == 2 and labels[""]["trades"] == 1


async def test_pump_trade_is_tagged(tmp_path, monkeypatch):
    async with aiohttp.ClientSession() as session:
        bot = Bot(Config(data_dir=str(tmp_path)), session)

        async def ok(mint):
            return SafetyResult(ok=True)

        prices = iter([1.0, 0.75])

        async def snaps(mints):
            p = next(prices)
            return {
                "P": snap(
                    mint="P",
                    price_usd=p,
                    change_h1=400,
                    liquidity_usd=80_000,
                    market_cap_usd=500_000,
                    volume_h1=400_000,
                )
            }

        monkeypatch.setattr(bot.rugcheck, "check", ok)
        monkeypatch.setattr(bot.dex, "snapshots", snaps)
        bot.watchlist["P"] = Candidate(mint="P", source="test")
        await bot.evaluate_once(NOW)
        assert bot.armed["P"].pump and bot.radar["P"]["armed"]["pump"]
        await bot.evaluate_once(NOW + 20)
        pos = bot.state.positions["P"]
        assert pos.entry_snapshot["mode"] == "pump"
        assert mode_stats([pos])[0]["label"] == "Pump con excepcion"


# ---------------- Marin v3 ----------------


def test_v3_waits_for_bounce_before_buying():
    cfg = FilterConfig()  # v3 por defecto: rebote de +4% y 5 min positivos
    a = Armed("M", "X", high=1.0, armed_at=NOW, score=70)
    assert check_pullback(a, snap(price_usd=0.85, change_m5=-6), cfg, NOW) == "wait"  # cayendo
    assert (
        check_pullback(a, snap(price_usd=0.80, change_m5=-8), cfg, NOW) == "wait"
    )  # sigue cayendo
    assert "rebote" in a.status and a.low == 0.80
    assert check_pullback(a, snap(price_usd=0.82, change_m5=1), cfg, NOW) == "wait"  # +2.5%, poco
    assert (
        check_pullback(a, snap(price_usd=0.84, change_m5=-2), cfg, NOW) == "wait"
    )  # 5 min negativo
    assert "cayendo" in a.status
    assert check_pullback(a, snap(price_usd=0.84, change_m5=2), cfg, NOW) == "buy"  # +5% y positivo
    assert 19 < a.last_dd < 21 and "rebote" in a.status


def test_v3_new_high_resets_low():
    cfg = FilterConfig()
    a = Armed("M", "X", high=1.0, armed_at=NOW, score=70)
    check_pullback(a, snap(price_usd=0.85), cfg, NOW)
    check_pullback(a, snap(price_usd=1.2), cfg, NOW)  # nuevo maximo
    assert a.high == 1.2 and a.low == 1.2


def test_v3_exits_lock_profit_earlier():
    cfg = ExitConfig()
    a = decide_exit(position(), 1.52, 50_000, NOW, cfg)
    assert a.tp_index == 0 and abs(a.fraction_of_remaining - 0.4) < 1e-9  # 40% al 1.5x
    # llego a 1.6x y cae 20% -> trailing (antes se iba hasta 1.08x)
    pos = position(peak_price_usd=1.6, tp_done=[0])
    a = decide_exit(pos, 1.28, 50_000, NOW, cfg)
    assert a and "trailing" in a.reason
    assert "stop loss" in decide_exit(position(), 0.77, 50_000, NOW, cfg).reason  # -23%


def test_v3_preset_and_mode_label(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps(
            {
                "filters": {"pullback_bounce_pct": 0, "max_price_change_h1_pct": 150},
                "exits": {"stop_loss_pct": 30},
            }
        )
    )
    keys = {k for k, _, _ in preset_changes(p, "marin-v3")}
    assert {
        "filters.pullback_bounce_pct",
        "exits.stop_loss_pct",
        "filters.max_price_change_h1_pct",
    } <= keys
    apply_preset(p, "marin-v3")
    cfg = load_config(p, env_file=None)
    assert cfg.exits.take_profit_ladder == [[1.5, 40.0], [2.5, 30.0], [5.0, 15.0]]
    assert cfg.execution.price_poll_seconds == 2


async def test_v3_bot_buys_after_bounce_and_tags_mode(tmp_path, monkeypatch):
    async with aiohttp.ClientSession() as session:
        bot = Bot(Config(data_dir=str(tmp_path)), session)

        async def ok(mint):
            return SafetyResult(ok=True)

        seq = iter([(1.0, 10), (0.82, -5), (0.80, -7), (0.84, 3)])

        async def snaps(mints):
            price, m5 = next(seq)
            return {"MINT1": snap(price_usd=price, change_m5=m5)}

        monkeypatch.setattr(bot.rugcheck, "check", ok)
        monkeypatch.setattr(bot.dex, "snapshots", snaps)
        bot.watchlist["MINT1"] = Candidate(mint="MINT1", source="test")
        for k in range(3):
            await bot.evaluate_once(NOW + 20 * k)
            assert not bot.state.positions  # marca, cae, sigue cayendo: no compra
        await bot.evaluate_once(NOW + 60)  # rebota +5% -> compra
        pos = bot.state.positions["MINT1"]
        assert pos.entry_snapshot["mode"] == "rebote"
        assert mode_stats([pos])[0]["label"] == "Rebote confirmado (v3)"
