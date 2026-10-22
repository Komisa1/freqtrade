"""Instinto de supervivencia: miedo, muerte y revivir."""

import time

import aiohttp
import pytest
from helpers import position, snap, v2_config

from memebot import __main__ as cli
from memebot.bot import Bot
from memebot.config import Config, RiskConfig, SurvivalConfig, load_config, validate
from memebot.dashboard import build_state
from memebot.models import Candidate
from memebot.risk import can_open
from memebot.safety import SafetyResult
from memebot.state import State
from memebot.survival import (
    Life,
    compute_instinct,
    fear_level,
    life_equity,
    mood_of,
    record_close,
    score_gate,
    should_die,
)

SV = SurvivalConfig()
RISK = RiskConfig()


def born_life(capital=2.0, **kw) -> Life:
    return Life(born_at=time.time() - 3600, capital=capital, peak=capital, **kw)


def test_fear_grows_with_drawdown_and_streak():
    assert fear_level(0, 0, SV) == 0
    assert fear_level(5, 0, SV) == 0  # todavia no tiene miedo
    assert fear_level(22.5, 0, SV) == pytest.approx(0.5)
    assert fear_level(40, 0, SV) == 1
    assert fear_level(80, 0, SV) == 1
    assert fear_level(0, 2, SV) == pytest.approx(0.2)  # dos perdidas seguidas: susto
    assert fear_level(0, 99, SV) == pytest.approx(0.5)  # el susto tiene tope
    assert fear_level(22.5, 2, SV) == pytest.approx(1 - 0.5 * 0.8)


def test_moods():
    assert mood_of(0)[0] == "tranquila"
    assert mood_of(0.3)[0] == "nerviosa"
    assert mood_of(0.5)[0] == "asustada"
    assert mood_of(1.0)[0] == "aterrada"
    assert mood_of(0.1, dead=True) == ("muerta", "💀")


def test_calm_instinct_changes_nothing():
    inst = compute_instinct(born_life(), [], SV, RISK)
    assert inst.fear == 0 and inst.mood == "tranquila" and not inst.dead
    assert inst.size_mult == 1 and inst.score_bonus == 0
    assert inst.max_open == RISK.max_open_positions
    assert inst.cooldown_minutes == RISK.cooldown_minutes_after_loss
    assert inst.min_regime == 0
    assert inst.death_line == pytest.approx(2.0 * 0.6)


def test_fear_makes_her_careful():
    life = born_life(realized_sol=-0.6)  # 2.0 -> 1.4: -30% desde su mejor momento
    inst = compute_instinct(life, [], SV, RISK)
    assert inst.drawdown_pct == pytest.approx(30)
    assert inst.fear == pytest.approx(25 / 35)
    assert inst.mood == "aterrada"
    assert inst.size_mult == pytest.approx(1 - inst.fear * 0.7)
    assert inst.score_bonus == pytest.approx(inst.fear * 20)
    assert inst.max_open == 1
    assert inst.cooldown_minutes == pytest.approx(10 * (1 + inst.fear * 3))
    assert inst.min_regime == SV.fear_regime_min
    assert not should_die(inst, SV)


def test_open_losses_count_but_open_gains_do_not_raise_the_peak():
    life = born_life(capital=1.0)
    # posicion de esta vida que va 3x: el patrimonio sube, pero el "mejor momento" no
    winner = position(entry_time=time.time(), sol_spent=0.1, last_price_usd=3.0)
    inst = compute_instinct(life, [winner], SV, RISK)
    assert inst.equity == pytest.approx(1.2)
    assert inst.peak == 1.0 and inst.fear == 0
    # la misma posicion cayendo a 0.1x: eso si le da miedo
    loser = position(entry_time=time.time(), sol_spent=0.5, last_price_usd=0.1)
    inst = compute_instinct(life, [loser], SV, RISK)
    assert inst.equity == pytest.approx(0.55)
    assert should_die(inst, SV)
    # posiciones de una vida anterior no cuentan
    old = position(entry_time=life.born_at - 10, sol_spent=0.5, last_price_usd=0.1)
    assert life_equity(life, [old]) == 1.0


def test_record_close_tracks_this_life_only():
    life = born_life(capital=1.0)
    win = position(mint="W", entry_time=time.time(), sol_spent=0.1, sol_received=0.3)
    loss = position(mint="L", entry_time=time.time(), sol_spent=0.1, sol_received=0.05)
    old = position(mint="O", entry_time=life.born_at - 10, sol_spent=0.1)
    record_close(life, loss)
    record_close(life, loss)
    assert life.loss_streak == 2 and life.realized_sol == pytest.approx(-0.1)
    record_close(life, win)
    assert life.loss_streak == 0 and life.trades == 3
    assert life.peak == pytest.approx(1.1)  # nuevo mejor momento cobrado
    record_close(life, old)
    assert life.trades == 3
    record_close(None, win)  # sin vida (desactivado) no truena


def test_can_open_with_fear(tmp_path):
    st = State(tmp_path, "paper")
    calm = compute_instinct(born_life(), [], SV, RISK)
    assert can_open("A", st, 1.0, RISK, instinct=calm)[0]

    dead = compute_instinct(born_life(dead=True), [], SV, RISK)
    ok, why = can_open("A", st, 1.0, RISK, instinct=dead)
    assert not ok and "murió" in why

    scared = compute_instinct(born_life(realized_sol=-0.6), [], SV, RISK)
    st.open_position(position(mint="X"))
    ok, why = can_open("A", st, 1.0, RISK, instinct=scared)
    assert not ok and "miedo" in why and "1 posicion" in why
    st.positions.clear()

    ok, why = can_open("A", st, 1.0, RISK, instinct=scared, regime_score=30)
    assert not ok and "mercado en contra" in why
    assert can_open("A", st, 1.0, RISK, instinct=scared, regime_score=60)[0]
    assert can_open("A", st, 1.0, RISK, instinct=scared, regime_score=None)[0]

    st.last_loss_time = time.time() - 15 * 60  # paso el descanso normal (10 min)...
    assert can_open("A", st, 1.0, RISK)[0]
    ok, why = can_open("A", st, 1.0, RISK, instinct=scared)  # ...pero con miedo descansa mas
    assert not ok and "descansa" in why

    # el saldo se revisa con el tamano real (con miedo, menor)
    st.last_loss_time = 0
    assert not can_open("A", st, 0.12, RISK)[0]
    assert can_open("A", st, 0.12, RISK, trade_size=0.05)[0]


def test_score_gate():
    scared = compute_instinct(born_life(realized_sol=-0.6), [], SV, RISK)
    assert score_gate(65, 60, None)[0]
    assert score_gate(65, 60, compute_instinct(born_life(), [], SV, RISK))[0]
    ok, why = score_gate(65, 60, scared)
    assert not ok and "puntaje" in why
    assert score_gate(80, 60, scared)[0]


def test_life_is_saved(tmp_path):
    st = State(tmp_path, "paper")
    st.life = born_life(capital=1.5, loss_streak=2)
    st.save()
    again = State(tmp_path, "paper")
    assert again.life == st.life
    assert State(tmp_path, "live").life is None


def test_config_validation():
    validate(Config())
    with pytest.raises(ValueError, match="survival"):
        validate(Config(survival=SurvivalConfig(fear_start_pct=50, death_drawdown_pct=40)))
    with pytest.raises(ValueError, match="survival"):
        validate(Config(survival=SurvivalConfig(death_drawdown_pct=100)))
    with pytest.raises(ValueError, match="min_size_pct"):
        validate(Config(survival=SurvivalConfig(min_size_pct=0)))


def test_example_config_has_survival():
    cfg = load_config("config.example.json", env_file=None)
    assert cfg.survival == SurvivalConfig()


async def _bot(tmp_path, monkeypatch, **survival):
    cfg = v2_config(tmp_path)
    cfg.survival = SurvivalConfig(**survival)
    session = aiohttp.ClientSession()
    bot = Bot(cfg, session)

    async def fake_check(mint):
        return SafetyResult(ok=True)

    monkeypatch.setattr(bot.rugcheck, "check", fake_check)
    return bot, session


async def test_bot_is_born_and_scared_bets_less(tmp_path, monkeypatch):
    bot, session = await _bot(tmp_path, monkeypatch)
    async with session:
        await bot.ensure_life()
        life = bot.state.life
        assert life.number == 1 and life.born and life.capital == pytest.approx(2.0)
        await bot.ensure_life()  # no vuelve a nacer
        assert bot.state.life is life

        life.realized_sol = -0.5  # -25%: con miedo
        await bot.check_life()
        assert bot.instinct.mood in ("asustada", "aterrada")
        s = snap(price_usd=1.0, mint="M1")
        await bot._try_buy(Candidate(mint="M1", source="test"), s, 95)
        pos = bot.state.positions["M1"]
        fee = bot.trader.fee_sol
        assert pos.sol_spent - fee == pytest.approx(0.1 * bot.instinct.size_mult)
        assert pos.entry_snapshot["fear"] == round(100 * bot.instinct.fear)

        # con miedo no le alcanza un puntaje que normalmente si
        bot.state.positions.clear()
        s2 = snap(price_usd=1.0, mint="M2")
        await bot._try_buy(Candidate(mint="M2", source="test"), s2, 61)
        assert "M2" not in bot.state.positions


async def test_bot_dies_sells_everything_and_stays_dead(tmp_path, monkeypatch):
    bot, session = await _bot(tmp_path, monkeypatch, life_capital_sol=0.2, death_confirm_seconds=0)
    async with session:
        await bot.ensure_life()
        s = snap(price_usd=1.0, mint="M1")
        await bot._try_buy(Candidate(mint="M1", source="test"), s, 95)
        pos = bot.state.positions["M1"]
        pos.last_price_usd = 0.2  # se desploma: el patrimonio de vida cae mas de 40%
        await bot.check_life()
        life = bot.state.life
        assert life.dead and "perdio" in life.death_reason
        assert not bot.state.positions  # vendio todo
        assert bot.state.closed[-1].close_reason == "Marín murió: vende todo"
        assert life.trades == 1 and life.realized_sol < 0

        bot.state.last_loss_time = 0  # sin el descanso normal: solo la muerte la frena
        await bot.evaluate_once()
        assert "murió" in bot.buy_block
        s2 = snap(price_usd=1.0, mint="M2")
        await bot._try_buy(Candidate(mint="M2", source="test"), s2, 99)
        assert "M2" not in bot.state.positions
        info = build_state(bot)["life"]
        assert info["dead"] and info["emoji"] == "💀" and info["life_left_pct"] == 0

    # reiniciar el bot no la revive
    bot2, session2 = await _bot(
        tmp_path, monkeypatch, life_capital_sol=0.2, death_confirm_seconds=0
    )
    async with session2:
        await bot2.ensure_life()
        await bot2.check_life()
        assert bot2.state.life.dead and bot2.instinct.dead
        ok, why = can_open("M3", bot2.state, 2.0, bot2.cfg.risk, instinct=bot2.instinct)
        assert not ok

        ok, msg = await bot2.revive()
        assert ok and "vida 2" in msg
        life = bot2.state.life
        assert life.number == 2 and life.born and not life.dead
        assert life.capital == pytest.approx(0.2) and life.trades == 0
        bot2._update_buy_block()
        assert bot2.buy_block and "murió" not in bot2.buy_block  # solo el descanso normal
        bot2.state.last_loss_time = 0
        bot2.last_wallet_sol = await bot2.trader.wallet_sol()
        bot2._update_buy_block()
        assert bot2.buy_block == ""
        ok, _ = await bot2.revive()
        assert not ok  # ya esta viva


async def test_survival_disabled(tmp_path, monkeypatch):
    bot, session = await _bot(tmp_path, monkeypatch, enabled=False)
    async with session:
        await bot.ensure_life()
        await bot.check_life()
        assert bot.state.life is None and bot.instinct is None
        assert build_state(bot)["life"] is None
        s = snap(price_usd=1.0)
        await bot._try_buy(Candidate(mint=s.mint, source="test"), s, 61)
        assert s.mint in bot.state.positions


def test_cli_revive(tmp_path, monkeypatch, capsys):
    cfg = Config(data_dir=str(tmp_path))
    monkeypatch.setattr(cli, "_bot_running", lambda cfg, mode: False)
    cli._revive(cfg, "paper", None)
    assert "todavia no nace" in capsys.readouterr().out

    st = State(tmp_path, "paper")
    st.life = born_life(capital=2.0)
    st.paper_balance = 1.1
    st.save()
    cli._revive(cfg, "paper", None)
    assert "está viva" in capsys.readouterr().out

    st.life.dead = True
    st.life.death_reason = "perdio 41%"
    st.save()
    monkeypatch.setattr("builtins.input", lambda _msg: "no")
    cli._revive(cfg, "paper", 2.0)
    assert "sigue muerta" in capsys.readouterr().out
    assert State(tmp_path, "paper").life.dead

    monkeypatch.setattr("builtins.input", lambda _msg: "SI")
    cli._revive(cfg, "paper", 2.0)
    assert "vida 2" in capsys.readouterr().out
    after = State(tmp_path, "paper")
    assert after.life == Life(number=2) and after.paper_balance == 2.0

    monkeypatch.setattr(cli, "_bot_running", lambda cfg, mode: True)
    cli._revive(cfg, "paper", None)
    assert "corriendo" in capsys.readouterr().out
    cli._revive(cfg, "paper", -1)
    assert "mayor que 0" in capsys.readouterr().out


def test_report_shows_life(tmp_path, capsys):
    cfg = Config(data_dir=str(tmp_path))
    st = State(tmp_path, "paper")
    st.life = born_life(capital=2.0, realized_sol=-0.3)
    st.save()
    cli._report(cfg, "paper")
    out = capsys.readouterr().out
    assert "Vida 1" in out and "miedo" in out and "muere en 1.2000 SOL" in out


async def test_death_needs_confirmation(tmp_path, monkeypatch):
    """Un precio raro por unos segundos no la mata; si sigue abajo, si."""
    bot, session = await _bot(tmp_path, monkeypatch, life_capital_sol=0.2)
    clock = [time.time()]
    monkeypatch.setattr("memebot.bot.time.time", lambda: clock[0])
    async with session:
        await bot.ensure_life()
        s = snap(price_usd=1.0, mint="M1")
        await bot._try_buy(Candidate(mint="M1", source="test"), s, 95)
        pos = bot.state.positions["M1"]
        pos.last_price_usd = 0.2
        await bot.check_life()
        assert not bot.state.life.dead and "M1" in bot.state.positions
        clock[0] += 10
        pos.last_price_usd = 1.0  # era un precio raro: se recupera
        await bot.check_life()
        assert not bot.state.life.dead
        pos.last_price_usd = 0.2
        clock[0] += 10
        await bot.check_life()  # vuelve a empezar a contar
        clock[0] += 19
        await bot.check_life()
        assert not bot.state.life.dead
        clock[0] += 2
        await bot.check_life()
        assert bot.state.life.dead and not bot.state.positions


async def test_no_buy_if_she_dies_during_rugcheck(tmp_path, monkeypatch):
    bot, session = await _bot(tmp_path, monkeypatch)
    async with session:
        await bot.ensure_life()
        await bot.check_life()

        async def check_and_die(mint):
            bot.state.life.dead = True  # muere mientras RugCheck responde
            return SafetyResult(ok=True)

        monkeypatch.setattr(bot.rugcheck, "check", check_and_die)
        s = snap(price_usd=1.0)
        await bot._try_buy(Candidate(mint=s.mint, source="test"), s, 95)
        assert not bot.state.positions


async def test_failed_revive_keeps_her_dead(tmp_path, monkeypatch):
    bot, session = await _bot(tmp_path, monkeypatch)
    async with session:
        await bot.ensure_life()
        bot.state.life.dead = True
        await bot.check_life()

        async def broken():
            raise RuntimeError("RPC caido")

        monkeypatch.setattr(bot.trader, "wallet_sol", broken)
        ok, msg = await bot.revive()
        assert not ok and "sigue muerta" in msg
        assert bot.state.life.number == 1 and bot.state.life.dead and bot.instinct.dead


async def test_no_birth_without_money(tmp_path, monkeypatch):
    bot, session = await _bot(tmp_path, monkeypatch)
    async with session:
        bot.trader.balance_sol = 0.05  # no alcanza para una compra de 0.1
        await bot.check_life()
        assert bot.state.life is None and bot.instinct is None
        bot.trader.balance_sol = 1.0  # deposito
        await bot.check_life()
        assert bot.state.life.born and bot.state.life.capital == 1.0
        assert bot.instinct is not None

        bot.state.life.dead = True
        bot.trader.balance_sol = 0.01
        ok, msg = await bot.revive()
        assert not ok and "deposita" in msg and bot.state.life.dead


async def test_inherited_positions_are_not_in_the_life(tmp_path, monkeypatch):
    bot, session = await _bot(tmp_path, monkeypatch)
    async with session:
        old = position(mint="OLD", entry_time=time.time() - 600, last_price_usd=1.0)
        bot.state.positions["OLD"] = old
        await bot.ensure_life()
        assert bot.state.life.capital == pytest.approx(2.0)  # solo el saldo
        old.last_price_usd = 0.01  # se desploma una moneda de antes: no es de esta vida
        await bot.check_life()
        assert bot.instinct.fear == 0


async def test_fear_size_has_a_floor_and_pump_needs_both_scores(tmp_path, monkeypatch):
    bot, session = await _bot(tmp_path, monkeypatch)
    async with session:
        await bot.ensure_life()
        bot.state.life.realized_sol = -0.79  # casi muerta: miedo ~100%
        await bot.check_life()
        assert bot.instinct.size_mult < 0.5
        assert bot._trade_size() == pytest.approx(0.05)  # piso por comisiones
        bot.cfg.filters.min_score = 70
        bot.cfg.filters.pump_min_score = 60
        ok, why = bot._buy_allowed("X", 2.0, 85, pump=True)
        assert not ok and "puntaje 85 < 90" in why


async def test_partial_sell_counts_in_life_equity(tmp_path, monkeypatch):
    bot, session = await _bot(tmp_path, monkeypatch)
    async with session:
        await bot.ensure_life()
        s = snap(price_usd=1.0)
        await bot._try_buy(Candidate(mint=s.mint, source="test"), s, 95)
        pos = bot.state.positions[s.mint]
        await bot.manage_position(pos, 2.2, 50_000)  # vende la mitad a 2.2x
        assert pos.tp_done == [0] and s.mint in bot.state.positions
        await bot.check_life()
        assert bot.instinct.equity > bot.state.life.capital + 0.1  # ~ +0.11 SOL de ganancia
        assert bot.instinct.fear == 0


async def test_dead_without_sell_all_keeps_positions(tmp_path, monkeypatch):
    bot, session = await _bot(
        tmp_path,
        monkeypatch,
        life_capital_sol=0.2,
        death_confirm_seconds=0,
        sell_all_on_death=False,
    )
    async with session:
        await bot.ensure_life()
        s = snap(price_usd=1.0)
        await bot._try_buy(Candidate(mint=s.mint, source="test"), s, 95)
        bot.state.positions[s.mint].last_price_usd = 0.2
        await bot.check_life()
        assert bot.state.life.dead and s.mint in bot.state.positions


async def test_dashboard_revive_endpoint(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from memebot.dashboard import create_app

    bot, session = await _bot(tmp_path, monkeypatch)
    async with session:
        await bot.ensure_life()
        bot.state.life.dead = True
        await bot.check_life()
        app = create_app(bot)
        async with TestClient(TestServer(app)) as client:
            html = await (await client.get("/")).text()
            token = html.split('const TOKEN = "')[1].split('"')[0]
            r = await client.post("/api/revive")
            assert r.status == 403 and bot.state.life.dead
            r = await client.post("/api/revive", headers={"X-Token": token})
            assert r.status == 200 and (await r.json())["ok"]
            assert bot.state.life.number == 2 and not bot.state.life.dead
            state = await (await client.get("/api/state")).json()
            assert state["life"]["number"] == 2 and state["life"]["mood"] == "tranquila"
            r = await client.post("/api/revive", headers={"X-Token": token})
            assert r.status == 400


def test_state_ignores_unknown_life_fields(tmp_path):
    import json

    st = State(tmp_path, "paper")
    st.life = born_life()
    st.save()
    data = json.loads(st.path.read_text())
    data["life"]["something_new"] = 1
    st.path.write_text(json.dumps(data))
    assert State(tmp_path, "paper").life.capital == 2.0


def test_heartbeat_detects_running_bot(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    cfg.dashboard.enabled = False
    st = State(tmp_path, "paper")
    assert not cli._bot_running(cfg, "paper")
    st.heartbeat()
    assert cli._bot_running(cfg, "paper") and not cli._bot_running(cfg, "live")
    st.heartbeat(stop=True)
    assert not cli._bot_running(cfg, "paper")
