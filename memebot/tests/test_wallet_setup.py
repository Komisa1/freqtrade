from memebot.config import Config, RiskConfig
from memebot.solana import load_keypair
from memebot.wallet_setup import preflight, read_env_key, risk_warnings, setup_wallet


def test_setup_wallet_writes_env_once(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comentario\nSOLANA_PRIVATE_KEY=\nSOLANA_RPC_URL=https://x\n", encoding="utf-8"
    )
    addr, created = setup_wallet(env)
    assert created
    key = read_env_key(env)
    assert str(load_keypair(key).pubkey()) == addr
    assert "SOLANA_RPC_URL=https://x" in env.read_text()
    addr2, created2 = setup_wallet(env)  # no sobreescribe una wallet existente
    assert addr2 == addr and not created2


def test_setup_wallet_without_env(tmp_path):
    addr, created = setup_wallet(tmp_path / ".env")
    assert created and addr


def test_risk_warnings():
    cfg = Config(risk=RiskConfig(trade_size_sol=0.1, max_open_positions=3, max_daily_loss_sol=0.5))
    assert any("no alcanza" in w for w in risk_warnings(0.1, cfg))
    warns = risk_warnings(0.4, cfg)
    assert any("20%" in w for w in warns) and any("diaria" in w for w in warns)
    assert any(
        "comisiones" in w for w in risk_warnings(1.0, Config(risk=RiskConfig(trade_size_sol=0.02)))
    )
    ok = Config(risk=RiskConfig(trade_size_sol=0.05, max_open_positions=3, max_daily_loss_sol=0.2))
    assert risk_warnings(2.0, ok) == []


async def test_preflight_without_key():
    checks = await preflight(Config())
    assert checks[0][0] == "FALTA"


def test_import_wallet(tmp_path):
    import pytest
    from solders.keypair import Keypair

    from memebot.wallet_setup import import_wallet

    env = tmp_path / ".env"
    env.write_text("SOLANA_PRIVATE_KEY=\n", encoding="utf-8")
    kp = Keypair()
    assert import_wallet(env, f"  {kp}  ") == str(kp.pubkey())
    assert read_env_key(env) == str(kp)
    with pytest.raises(ValueError):
        import_wallet(env, "no-es-una-clave")
    assert read_env_key(env) == str(kp)  # una clave invalida no borra la anterior


def test_capital_and_total_loss_caps(tmp_path):
    import time

    from helpers import position

    from memebot.risk import can_open
    from memebot.state import State

    st = State(tmp_path, "live")
    cfg = RiskConfig(trade_size_sol=0.1, max_capital_sol=0.25, max_open_positions=5)
    st.positions["A"] = position(mint="A", sol_spent=0.1)
    st.positions["B"] = position(mint="B", sol_spent=0.1)
    ok, why = can_open("C", st, 5.0, cfg)
    assert not ok and "capital" in why
    st.positions["B"].sol_received = 0.1  # ya recupero lo invertido -> libera capital
    assert can_open("C", st, 5.0, cfg)[0]

    st2 = State(tmp_path / "x", "live")
    st2.closed.append(
        position(mint="L", sol_spent=0.3, closed=True, close_time=time.time() - 3 * 86400)
    )
    ok, why = can_open("D", st2, 5.0, RiskConfig(max_total_loss_sol=0.25))
    assert not ok and "perdida total" in why


def test_recommended_limits_and_apply(tmp_path):
    import json

    from memebot.config import load_config
    from memebot.wallet_setup import apply_limits, recommended_limits

    lim = recommended_limits(0.3164)
    assert lim["trade_size_sol"] == 0.05 and lim["max_open_positions"] == 3
    assert lim["max_capital_sol"] <= 0.3164 / 2 + 1e-9
    cfg = Config(risk=RiskConfig(**lim))
    assert risk_warnings(0.3164, cfg) == []
    tiny = recommended_limits(0.12)
    assert tiny["max_open_positions"] == 1
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"_nota": "x", "risk": {"cooldown_minutes_after_loss": 5}}))
    apply_limits(path, lim)
    loaded = load_config(path, env_file=None)
    assert loaded.risk.trade_size_sol == 0.05 and loaded.risk.cooldown_minutes_after_loss == 5
