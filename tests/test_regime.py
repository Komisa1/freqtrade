import time

from helpers import position

from memebot.config import RiskConfig
from memebot.regime import RegimeTracker, meme_breadth, score_regime, sol_trend_score
from memebot.risk import can_open
from memebot.state import State

NOW = time.time()


def test_sol_trend():
    up = [100 + i for i in range(30)]
    down = [130 - i for i in range(30)]
    assert sol_trend_score(up) == 100
    assert sol_trend_score(down) == 10
    assert sol_trend_score([1, 2]) is None


def test_score_regime_zones_and_missing_parts():
    good = score_regime([100 + i for i in range(30)], 6, 4, 68)
    assert good["zone"] == "a favor" and good["score"] >= 65
    bad = score_regime([130 - i for i in range(30)], -7, -4, 25)
    assert bad["zone"] == "en contra" and bad["score"] < 40
    only_radar = score_regime(None, None, None, 50)  # sin CoinGecko: solo el radar
    assert only_radar["score"] == 50 and only_radar["parts"]["sol_trend"] is None
    assert score_regime(None, None, None, None)["score"] is None


def test_meme_breadth_needs_sample():
    radar = {str(i): {"t": NOW, "chg_h1": 10 if i % 4 else -5} for i in range(40)}
    assert meme_breadth(radar, NOW) == 75
    assert meme_breadth({k: radar[k] for k in list(radar)[:5]}, NOW) is None
    old = {k: {**v, "t": NOW - 7200} for k, v in radar.items()}
    assert meme_breadth(old, NOW) is None


async def test_tracker_without_coingecko_uses_radar():
    tr = RegimeTracker(session=None)
    radar = {str(i): {"t": NOW, "chg_h1": 5} for i in range(30)}
    assert tr.update(radar, NOW)["score"] == 100


def test_regime_filter_and_losing_streak(tmp_path):
    st = State(tmp_path, "live")
    cfg = RiskConfig(regime_min_score=50)
    assert can_open("X", st, 5, cfg, regime_score=60)[0]
    ok, why = can_open("X", st, 5, cfg, regime_score=30)
    assert not ok and "mercado en contra" in why
    assert can_open("X", st, 5, cfg, regime_score=None)[0]  # sin dato no bloquea
    # 4 perdidas seguidas -> pausa en real, no en simulacion
    for i in range(4):
        st.closed.append(
            position(mint=f"L{i}", closed=True, close_time=NOW - 300, sol_received=0.05)
        )
    cfg2 = RiskConfig(
        max_consecutive_losses=4, streak_pause_minutes=60, cooldown_minutes_after_loss=0
    )
    ok, why = can_open("X", st, 5, cfg2, now=NOW, live=True)
    assert not ok and "racha de 4" in why
    assert can_open("X", st, 5, cfg2, now=NOW, live=False)[0]
    assert can_open("X", st, 5, cfg2, now=NOW + 3700, live=True)[0]  # ya paso la hora
    st.closed.append(position(mint="W", closed=True, close_time=NOW - 200, sol_received=0.2))
    assert can_open("X", st, 5, cfg2, now=NOW, live=True)[0]  # una ganadora rompe la racha
