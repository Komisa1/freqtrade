import time

from helpers import snap

from memebot.config import FilterConfig
from memebot.scoring import Verdict, evaluate, score_snapshot


def test_good_token_is_buy():
    ev = evaluate(snap(), FilterConfig())
    assert ev.verdict is Verdict.BUY, ev.reasons
    assert ev.score >= 60


def test_too_young_waits():
    ev = evaluate(snap(pair_created_at=time.time() - 60), FilterConfig())
    assert ev.verdict is Verdict.WAIT
    assert any("nuevo" in r for r in ev.reasons)


def test_too_old_or_big_rejects():
    assert (
        evaluate(snap(pair_created_at=time.time() - 3 * 86400), FilterConfig()).verdict
        is Verdict.REJECT
    )
    assert evaluate(snap(market_cap_usd=50_000_000), FilterConfig()).verdict is Verdict.REJECT


def test_sell_pressure_waits():
    ev = evaluate(snap(buys_m5=50, sells_m5=120), FilterConfig())
    assert ev.verdict is Verdict.WAIT


def test_parabolic_is_not_chased():
    ev = evaluate(snap(change_h1=900), FilterConfig())
    assert ev.verdict is Verdict.WAIT
    assert score_snapshot(snap(change_m5=200)) < score_snapshot(snap(change_m5=25))


def test_pumpfun_without_liquidity_field_is_not_blocked():
    s = snap(dex_id="pumpfun", liquidity_usd=0, liquidity_known=False, market_cap_usd=60_000)
    ev = evaluate(s, FilterConfig())
    assert not any("liquidez" in r for r in ev.reasons)
