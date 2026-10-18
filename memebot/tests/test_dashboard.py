import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from helpers import position

from memebot.bot import Bot
from memebot.config import Config
from memebot.dashboard import build_state, create_app


async def test_state_and_manual_sell(tmp_path, monkeypatch):
    async with aiohttp.ClientSession() as session:
        bot = Bot(Config(data_dir=str(tmp_path)), session)
        pos = position(mint="M1", last_price_usd=1.5, peak_price_usd=1.6)
        bot.state.open_position(pos)

        async def no_snap(mint):
            return None

        monkeypatch.setattr(bot.dex, "snapshot", no_snap)
        state = build_state(bot)
        assert state["open"][0]["symbol"] == "PEPE"
        assert abs(state["open"][0]["multiple"] - 1.5) < 1e-9

        async with TestClient(TestServer(create_app(bot))) as client:
            html = await (await client.get("/")).text()
            assert "__API_TOKEN__" not in html
            token = html.split('const TOKEN = "')[1].split('"')[0]
            assert (await client.get("/static/lightweight-charts.js")).status == 200
            assert (await client.get("/api/state")).status == 200
            # sin token no se puede vender
            assert (await client.post("/api/sell/M1")).status == 403
            resp = await client.post("/api/sell/M1", headers={"X-Token": token})
            assert resp.status == 200, await resp.text()
        assert "M1" not in bot.state.positions
        assert bot.state.closed[-1].close_reason == "venta manual"
