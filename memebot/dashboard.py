"""Panel web local (http://127.0.0.1:8080): saldo, posiciones, PnL, radar y graficas en vivo.

Solo escucha en 127.0.0.1, asi que nadie fuera de tu computadora puede verlo. Las acciones
(vender) exigen un token aleatorio que solo conoce la pagina servida por el propio bot.
"""

from __future__ import annotations

import logging
import secrets
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .analysis import compare_winners_losers, mode_stats
from .models import Position
from .stages import STAGE_LABELS, STAGES
from .trader import LiveTrader

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("memebot")

WEB_DIR = Path(__file__).parent / "web"
BOT_DIR = Path(__file__).resolve().parents[1]  # carpeta donde esta start.bat
MASCOT_NAMES = ("mascota.png", "mascota.jpg", "mascota.jpeg", "mascota.webp", "mascota.gif")


def find_mascot() -> Path | None:
    """Imagen propia para reemplazar al cangrejo: un archivo 'mascota.*' en la carpeta del bot."""
    for folder in dict.fromkeys((BOT_DIR, Path.cwd())):
        for name in MASCOT_NAMES:
            path = folder / name
            if path.is_file():
                return path
    return None


def _pos_dict(p: Position) -> dict[str, Any]:
    price = p.last_price_usd or p.entry_price_usd
    value = 0.0 if p.closed else p.unrealized_value_sol(price)
    sold_frac = 1 - (p.tokens_left / p.tokens_initial if p.tokens_initial else 0)
    cost_left = p.sol_spent * (1 - sold_frac)
    return {
        "mint": p.mint,
        "symbol": p.symbol,
        "pair": p.pair_address,
        "source": p.source,
        "entry_price": p.entry_price_usd,
        "price": price,
        "peak_multiple": p.multiple(p.peak_price_usd),
        "multiple": p.multiple(price),
        "sol_spent": p.sol_spent,
        "sol_received": p.sol_received,
        "value_sol": value,
        "upnl_sol": value - cost_left,
        "total_pnl_sol": p.sol_received + value - p.sol_spent,
        "sold_pct": sold_frac * 100,
        "tp_done": len(p.tp_done),
        "entry_time": p.entry_time,
        "close_time": p.close_time,
        "close_reason": p.close_reason,
        "stage": p.stage,
        "closed": p.closed,
        "signatures": [s for s in p.signatures if s and s != "paper"][-5:],
    }


def stage_stats(closed: list[Position]) -> list[dict[str, Any]]:
    """Resultados por etapa de compra: para saber si conviene mas nuevas, a punto o migradas."""
    out = []
    for stage in (*STAGES, ""):
        ps = [p for p in closed if p.stage == stage]
        if not ps:
            continue
        spent = sum(p.sol_spent for p in ps)
        pnl = sum(p.realized_pnl_sol for p in ps)
        out.append(
            {
                "stage": stage,
                "label": STAGE_LABELS.get(stage, "Sin etapa (antes)"),
                "trades": len(ps),
                "wins": sum(1 for p in ps if p.realized_pnl_sol > 0),
                "pnl_sol": pnl,
                "roi_pct": 100 * pnl / spent if spent else 0.0,
                "best_x": max((p.sol_received / p.sol_spent for p in ps if p.sol_spent), default=0),
            }
        )
    return out


def build_trenches(bot: Bot) -> dict[str, list[dict[str, Any]]]:
    """Las tres columnas tipo GMGN. Mezcla eventos en vivo de pump.fun con los datos evaluados."""
    cols: dict[str, dict[str, dict[str, Any]]] = {s: {} for s in STAGES}
    for ev in bot.recent_new:
        if ev["mint"] in bot.radar:
            continue
        mcap = ev.get("mcap_sol", 0) * bot.sol_usd if bot.sol_usd else None
        cols["new"][ev["mint"]] = {**ev, "stage": "new", "mcap": mcap, "pending": True}
    for ev in bot.recent_migrated:
        if ev["mint"] not in bot.radar:
            item = {**ev, "stage": "migrated", "progress": 1.0, "pending": True}
            cols["migrated"][ev["mint"]] = item
    for r in bot.radar.values():
        cols[r.get("stage", "new")][r["mint"]] = r
    out = {}
    for stage, items in cols.items():
        if stage == "about":
            ordered = sorted(items.values(), key=lambda r: r.get("progress") or 0, reverse=True)
        else:
            ordered = sorted(items.values(), key=lambda r: r.get("t", 0), reverse=True)
        out[stage] = ordered[:40]
    return out


def _mascot_url() -> str | None:
    path = find_mascot()
    return f"/mascota?v={int(path.stat().st_mtime)}" if path else None


def build_state(bot: Bot) -> dict[str, Any]:
    st = bot.state
    now = time.time()
    open_pos = [_pos_dict(p) for p in st.positions.values()]
    closed = [_pos_dict(p) for p in st.closed[-300:]][::-1]
    closed_all = st.closed
    wins = sum(1 for p in closed_all if p.realized_pnl_sol > 0)

    # PnL realizado por dia (calendario)
    daily: dict[str, float] = {}
    for p in closed_all:
        day = datetime.fromtimestamp(p.close_time).strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0.0) + p.realized_pnl_sol

    # submuestreo de la curva de equity para no mandar miles de puntos
    eq = list(bot.equity)
    step = max(1, len(eq) // 1500)
    equity = [[t, v] for t, v in eq[::step]]
    if eq and (not equity or equity[-1][0] != eq[-1][0]):
        equity.append(list(eq[-1]))

    radar = sorted(
        bot.radar.values(), key=lambda r: (r["verdict"] == "BUY", r["score"]), reverse=True
    )
    return {
        "now": now,
        "mode": bot.mode,
        "wallet": getattr(bot.trader, "pubkey", "") or bot.prepared_wallet,
        "wallet_in_use": bool(getattr(bot.trader, "pubkey", "")),
        "sol_usd": bot.sol_usd,
        "balance_sol": bot.last_wallet_sol,
        "open_value_sol": sum(p["value_sol"] for p in open_pos),
        "pnl_today_sol": st.realized_pnl_today(now),
        "pnl_total_sol": sum(p.realized_pnl_sol for p in closed_all),
        "upnl_sol": sum(p["upnl_sol"] for p in open_pos),
        "trades_closed": len(closed_all),
        "wins": wins,
        "open": open_pos,
        "closed": closed,
        "daily": daily,
        "equity": equity,
        "radar": radar[:80],
        "trenches": build_trenches(bot),
        "stage_stats": stage_stats(closed_all),
        "mode_stats": mode_stats(closed_all),
        "compare": compare_winners_losers(closed_all),
        "armed_count": len(bot.armed),
        "regime": bot.regime.current,
        "entry_mode": bot.cfg.filters.entry_mode,
        "buy_stages": bot.state.buy_stages,
        "buy_block": bot.buy_block,
        "last_eval_at": bot.last_eval_at,
        "mascot": _mascot_url(),
        "events": list(bot.notifier.events)[-80:][::-1],
        "stats": {
            **bot.stats,
            "watching": len(bot.watchlist),
            "uptime_min": (now - bot.started_at) / 60,
        },
        "settings": {
            "trade_size_sol": bot.cfg.risk.trade_size_sol,
            "max_open_positions": bot.cfg.risk.max_open_positions,
            "stop_loss_pct": bot.cfg.exits.stop_loss_pct,
            "take_profit_ladder": bot.cfg.exits.take_profit_ladder,
            "trailing_stop_pct": bot.cfg.exits.trailing_stop_pct,
        },
    }


def create_app(bot: Bot) -> web.Application:
    token = secrets.token_urlsafe(24)
    index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

    async def index(_req: web.Request) -> web.Response:
        html = index_html.replace("__API_TOKEN__", token)
        return web.Response(
            text=html, content_type="text/html", headers={"Cache-Control": "no-store"}
        )

    async def api_state(_req: web.Request) -> web.Response:
        try:
            bot.last_wallet_sol = await bot.trader.wallet_sol()
        except Exception:  # noqa: BLE001 - mostramos el ultimo saldo conocido
            pass
        return web.json_response(build_state(bot))

    async def api_sell(req: web.Request) -> web.Response:
        if req.headers.get("X-Token") != token:
            return web.json_response({"ok": False, "error": "no autorizado"}, status=403)
        ok, msg = await bot.sell_now(req.match_info["mint"])
        return web.json_response({"ok": ok, "message": msg}, status=200 if ok else 400)

    async def api_buy_stages(req: web.Request) -> web.Response:
        if req.headers.get("X-Token") != token:
            return web.json_response({"ok": False, "error": "no autorizado"}, status=403)
        body = await req.json()
        stages = [s for s in body.get("stages", []) if s in STAGES]
        bot.state.buy_stages = stages
        bot.state.save()
        log.info("Etapas de compra cambiadas desde el panel: %s", stages or "ninguna")
        return web.json_response({"ok": True, "stages": stages})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/sell/{mint}", api_sell)
    app.router.add_post("/api/buy-stages", api_buy_stages)

    async def mascot(_req: web.Request) -> web.StreamResponse:
        path = find_mascot()
        if not path:
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "no-store"})

    async def api_chart(req: web.Request) -> web.Response:
        from .chart import build_chart

        data = await build_chart(bot, req.match_info["mint"])
        if data is None:
            return web.json_response({"error": "sin operaciones del bot en ese token"}, status=404)
        return web.json_response(data)

    app.router.add_get("/mascota", mascot)
    app.router.add_get("/api/chart/{mint}", api_chart)
    app.router.add_static("/static", WEB_DIR)
    return app


async def start_dashboard(bot: Bot) -> web.AppRunner | None:
    cfg = bot.cfg.dashboard
    runner = web.AppRunner(create_app(bot), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", cfg.port).start()
    except OSError as e:
        log.warning("No se pudo abrir el panel en el puerto %s: %s", cfg.port, e)
        return None
    url = f"http://127.0.0.1:{cfg.port}"
    kind = "REAL" if isinstance(bot.trader, LiveTrader) else "SIMULACION"
    log.info("Panel abierto en %s (modo %s)", url, kind)
    if cfg.open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    return runner
