import json
import time

import pytest
from helpers import position

from memebot.config import FilterConfig, RiskConfig, load_config
from memebot.risk import can_open
from memebot.safety import evaluate_report
from memebot.sources.dexscreener import best_pairs
from memebot.sources.gmgn import parse_rank
from memebot.sources.pumpportal import parse_message
from memebot.state import State


def test_risk_limits(tmp_path):
    st = State(tmp_path, "paper")
    cfg = RiskConfig(max_open_positions=1, max_daily_loss_sol=0.2)
    assert can_open("A", st, 1.0, cfg)[0]
    st.open_position(position(mint="A"))
    assert not can_open("B", st, 1.0, cfg)[0]  # maximo de posiciones
    p = st.positions["A"]
    p.sol_received = 0.0  # perdio todo
    st.close_position(p, "test")
    st.last_loss_time = 0
    st.closed.append(position(mint="C", sol_spent=0.2, closed=True, close_time=time.time()))
    ok, why = can_open("B", st, 1.0, cfg)
    assert not ok and "perdida diaria" in why
    assert not can_open("A", State(tmp_path, "paper"), 1.0, RiskConfig())[
        0
    ]  # ya operado (persistido)


def test_low_balance(tmp_path):
    ok, why = can_open("X", State(tmp_path, "paper"), 0.12, RiskConfig())
    assert not ok and "saldo" in why


def test_rugcheck_report():
    cfg = FilterConfig()
    good = {
        "score_normalised": 5,
        "risks": [{"name": "Low amount of LP Providers", "level": "warn"}],
    }
    assert evaluate_report(good, cfg).ok
    bad = {
        "score_normalised": 5,
        "risks": [{"name": "Freeze Authority still enabled", "level": "warn"}],
    }
    assert not evaluate_report(bad, cfg).ok
    assert not evaluate_report({"score_normalised": 80, "risks": []}, cfg).ok
    assert not evaluate_report(
        {"risks": [{"name": "Single holder ownership", "level": "danger"}]}, cfg
    ).ok


def test_parsers():
    pairs = [
        {
            "chainId": "solana",
            "pairAddress": "P1",
            "baseToken": {"address": "M", "symbol": "X"},
            "priceUsd": "0.01",
            "liquidity": {"usd": 1000},
            "pairCreatedAt": 1700000000000,
        },
        {
            "chainId": "solana",
            "pairAddress": "P2",
            "baseToken": {"address": "M", "symbol": "X"},
            "priceUsd": "0.01",
            "liquidity": {"usd": 9000},
            "txns": {"m5": {"buys": 3, "sells": 1}},
        },
    ]
    snaps = best_pairs(pairs)
    assert snaps["M"].pair_address == "P2" and snaps["M"].buys_m5 == 3
    assert parse_message({"txType": "create", "mint": "Z", "symbol": "Z"}).source == "pumpfun-new"
    assert parse_message({"txType": "migrate", "mint": "Z"}).source == "pumpfun-graduated"
    assert parse_message({"txType": "buy", "mint": "Z"}) is None
    assert parse_message({"message": "Successfully subscribed"}) is None
    assert parse_rank({"data": {"rank": [{"address": "G", "symbol": "G"}]}})[0].mint == "G"


def test_config_rejects_secrets_and_unknown_keys(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"private_key": "x"}))
    with pytest.raises(ValueError):
        load_config(p, env_file=None)
    p.write_text(json.dumps({"risk": {"trade_size": 1}}))
    with pytest.raises(ValueError):
        load_config(p, env_file=None)
    p.write_text(json.dumps({"_nota": "hola", "risk": {"trade_size_sol": 0.05}}))
    assert load_config(p, env_file=None).risk.trade_size_sol == 0.05


def test_example_config_loads():
    from pathlib import Path

    cfg = load_config(Path(__file__).parents[1] / "config.example.json", env_file=None)
    assert cfg.live is False
