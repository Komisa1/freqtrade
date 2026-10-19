import time

import aiohttp
from helpers import position, snap

from memebot import chart
from memebot.bot import Bot
from memebot.config import Config
from memebot.models import Candidate
from memebot.safety import SafetyResult


def test_candles_from_samples():
    c = chart.candles_from_samples([(0, 1.0), (10, 2.0), (50, 0.5), (65, 3.0)], 60)
    assert c == [[0, 1.0, 2.0, 0.5, 0.5], [60, 3.0, 3.0, 3.0, 3.0]]
    assert chart.pick_timeframe(3600)[2] == 60
    assert chart.pick_timeframe(10 * 3600)[2] == 300


async def test_build_chart_with_bot_prices_and_trades(tmp_path, monkeypatch):
    async def no_gecko(*a, **k):
        return None

    monkeypatch.setattr(chart, "gecko_candles", no_gecko)
    async with aiohttp.ClientSession() as session:
        bot = Bot(Config(data_dir=str(tmp_path)), session)

        async def ok(mint):
            return SafetyResult(ok=True)

        monkeypatch.setattr(bot.rugcheck, "check", ok)
        mint = "So1anaMint1111111111111111111111111111111pump"
        s = snap(mint=mint, price_usd=1.0, pair_address="")
        await bot._try_buy(Candidate(mint=mint, source="t"), s, 80, "migrated")
        pos = bot.state.positions[mint]
        bot.state.record_price(mint, time.time() + 30, 2.2)
        await bot.manage_position(pos, 2.2, 50_000)  # take profit 2x
        data = await chart.build_chart(bot, mint)
        assert data["source"] == "bot" and data["candles"]
        sides = [t["side"] for t in data["trades"]]
        assert sides == ["buy", "sell"]
        assert data["stop_price"] == pos.entry_price_usd  # breakeven tras el TP
        assert [tp["label"] for tp in data["tp_prices"]] == ["TP 4x"]
        assert await chart.build_chart(bot, "../etc/passwd") is None
        assert await chart.build_chart(bot, position().mint + "x" * 40) is None
