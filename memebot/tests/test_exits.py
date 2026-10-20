import time

from helpers import position, v2_exits

from memebot.exits import decide_exit

CFG = v2_exits()
NOW = time.time()


def test_hold_when_nothing_happens():
    assert decide_exit(position(), 1.05, 50_000, NOW, CFG) is None


def test_stop_loss():
    a = decide_exit(position(), 0.69, 50_000, NOW, CFG)
    assert a and a.fraction_of_remaining == 1.0 and "stop loss" in a.reason


def test_take_profit_ladder_and_breakeven():
    pos = position()
    a = decide_exit(pos, 2.1, 50_000, NOW, CFG)
    assert a.tp_index == 0 and abs(a.fraction_of_remaining - 0.5) < 1e-9
    pos.tp_done.append(0)
    pos.tokens_left = 500
    pos.peak_price_usd = 2.1
    # segundo escalon: 25% de la original = 50% de lo que queda
    a = decide_exit(pos, 4.2, 50_000, NOW, CFG)
    assert a.tp_index == 1 and abs(a.fraction_of_remaining - 0.5) < 1e-9
    # despues de tomar ganancias, el stop esta en la entrada
    a = decide_exit(position(tp_done=[0], peak_price_usd=1.2), 0.99, 50_000, NOW, CFG)
    assert a and "breakeven" in a.reason


def test_trailing_stop():
    pos = position(peak_price_usd=3.0, tp_done=[0])
    a = decide_exit(pos, 2.2, 50_000, NOW, CFG)
    assert a and "trailing" in a.reason and a.fraction_of_remaining == 1.0


def test_liquidity_drain_exits():
    a = decide_exit(position(), 1.2, 1_000, NOW, CFG)
    assert a and "liquidez" in a.reason


def test_unknown_liquidity_does_not_exit():
    assert decide_exit(position(), 1.05, None, NOW, CFG) is None


def test_stale_and_max_hold():
    a = decide_exit(position(entry_time=NOW - 50 * 60), 1.02, 50_000, NOW, CFG)
    assert a and "sin movimiento" in a.reason
    a = decide_exit(position(entry_time=NOW - 300 * 60, peak_price_usd=1.4), 1.3, 50_000, NOW, CFG)
    assert a and "tiempo maximo" in a.reason
