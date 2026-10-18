"""Flujo completo en modo simulacion, sin red (fuentes mockeadas)."""

import time

import aiohttp
from helpers import snap

from memebot.bot import Bot
from memebot.config import Config
from memebot.models import Candidate
from memebot.safety import SafetyResult


async def test_paper_buy_then_take_profit_then_trailing(tmp_path, monkeypatch):
    cfg = Config(data_dir=str(tmp_path))
    async with aiohttp.ClientSession() as session:
        bot = Bot(cfg, session)

        async def fake_check(mint):
            return SafetyResult(ok=True)

        monkeypatch.setattr(bot.rugcheck, "check", fake_check)
        s = snap(price_usd=1.0)
        await bot._try_buy(Candidate(mint=s.mint, source="test"), s, 80)
        pos = bot.state.positions[s.mint]
        assert pos.sol_spent > 0.1 and bot.trader.balance_sol < 2.0

        await bot.manage_position(pos, 2.2, 50_000)  # TP 2x: vende la mitad
        assert pos.tp_done == [0] and abs(pos.tokens_left - pos.tokens_initial / 2) < 1e-9
        await bot.manage_position(pos, 3.0, 50_000)  # nuevo maximo, mantiene
        assert s.mint in bot.state.positions
        await bot.manage_position(pos, 2.0, 50_000)  # -33% desde el maximo: trailing
        assert s.mint not in bot.state.positions
        closed = bot.state.closed[-1]
        assert closed.realized_pnl_sol > 0.08  # ~2.2x en la mitad + ~2x en el resto
        assert (tmp_path / "trades_paper.csv").read_text().count("\n") == 4

        # no recompra el mismo token
        await bot._try_buy(Candidate(mint=s.mint, source="test"), s, 80)
        assert s.mint not in bot.state.positions


async def test_rug_rejected_is_not_bought(tmp_path, monkeypatch):
    async with aiohttp.ClientSession() as session:
        bot = Bot(Config(data_dir=str(tmp_path)), session)

        async def bad(mint):
            return SafetyResult(ok=False, reasons=["danger: Freeze Authority"])

        monkeypatch.setattr(bot.rugcheck, "check", bad)
        s = snap()
        await bot._try_buy(Candidate(mint=s.mint, source="test", first_seen=time.time()), s, 90)
        assert not bot.state.positions and s.mint in bot.rejected
