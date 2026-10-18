import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from helpers import snap

from memebot.bot import Bot
from memebot.config import Config
from memebot.dashboard import create_app, stage_stats
from memebot.models import Candidate
from memebot.stages import classify, progress_from_mcap_sol, progress_from_vsol
from memebot.state import State


def test_curve_progress():
    assert progress_from_vsol(30) == 0
    assert progress_from_vsol(115) == 1
    assert abs(progress_from_mcap_sol(28) - 0) < 0.01  # mcap inicial ~28 SOL
    assert progress_from_mcap_sol(412) > 0.99  # mcap al migrar ~412 SOL
    assert 0.3 < progress_from_mcap_sol(150) < 0.5


def test_classify():
    assert classify("pumpfun", "pumpfun-new", 0.2) == "new"
    assert classify("pumpfun", "pumpfun-new", 0.8) == "about"
    assert classify("pumpswap", "pumpfun-new", None) == "migrated"
    assert classify("pumpfun", "pumpfun-graduated", 0.99) == "migrated"
    assert classify("pumpfun", "pumpfun-new", 0.5, migrated_seen=True) == "migrated"


async def test_bot_stage_tracking_and_buy_filter(tmp_path):
    async with aiohttp.ClientSession() as session:
        bot = Bot(Config(data_dir=str(tmp_path)), session)
        bot.sol_usd = 150.0
        bot._track_stage_event(
            Candidate(
                mint="N", source="pumpfun-new", symbol="N", extra={"vsol": 40, "market_cap_sol": 35}
            )
        )
        assert bot.recent_new[-1]["progress"] > 0.1
        s = snap(mint="C", dex_id="pumpfun", market_cap_usd=300 * 150, liquidity_known=False)
        assert bot.stage_of(s, "pumpfun-new")[0] == "about"
        bot._track_stage_event(Candidate(mint="C", source="pumpfun-graduated"))
        assert bot.stage_of(s, "pumpfun-new") == ("migrated", 1.0)

        async with TestClient(TestServer(create_app(bot))) as client:
            html = await (await client.get("/")).text()
            token = html.split('const TOKEN = "')[1].split('"')[0]
            r = await client.post(
                "/api/buy-stages",
                json={"stages": ["migrated", "bogus"]},
                headers={"X-Token": token},
            )
            assert (await r.json())["stages"] == ["migrated"]
            assert (await client.post("/api/buy-stages", json={"stages": []})).status == 403
            state = await (await client.get("/api/state")).json()
            assert set(state["trenches"]) == {"new", "about", "migrated"}
        assert State(tmp_path, "paper").buy_stages == ["migrated"]  # persistido


def test_stage_stats():
    from helpers import position

    a = position(mint="a", stage="migrated", sol_received=0.3)
    b = position(mint="b", stage="new", sol_received=0.05)
    stats = {s["stage"]: s for s in stage_stats([a, b])}
    assert stats["migrated"]["wins"] == 1 and stats["new"]["wins"] == 0
