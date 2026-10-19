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
    warns = risk_warnings(0.5, cfg)
    assert any("15%" in w for w in warns) and any("diaria" in w for w in warns)
    ok = Config(risk=RiskConfig(trade_size_sol=0.05, max_open_positions=3, max_daily_loss_sol=0.2))
    assert risk_warnings(2.0, ok) == []


async def test_preflight_without_key():
    checks = await preflight(Config())
    assert checks[0][0] == "FALTA"
