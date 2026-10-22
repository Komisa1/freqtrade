"""Linea de comandos: python -m memebot <comando>"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import aiohttp

from .config import load_config
from .safety import RugCheck
from .scoring import evaluate
from .sources.dexscreener import DexScreener
from .state import State


def _setup_logging(level: str, data_dir: str) -> None:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(Path(data_dir) / "memebot.log", encoding="utf-8"),
        ],
    )


async def _run(cfg) -> None:
    from .bot import Bot

    async with aiohttp.ClientSession(trust_env=True) as session:
        await Bot(cfg, session).run()


async def _check(cfg, mint: str) -> None:
    async with aiohttp.ClientSession(trust_env=True) as session:
        snap = await DexScreener(session).snapshot(mint)
        if not snap:
            print("DexScreener no tiene datos de ese token (todavia).")
            return
        ev = evaluate(snap, cfg.filters, time.time())
        safety = await RugCheck(session, cfg.filters).check(mint)
        print(f"{snap.symbol} ({snap.name})  {snap.url}")
        print(
            f"  precio ${snap.price_usd:.10g} | mcap ${snap.market_cap_usd:,.0f} | "
            f"liq ${snap.liquidity_usd:,.0f} | edad {snap.age_minutes():.0f} min"
        )
        print(
            f"  5m: vol ${snap.volume_m5:,.0f}, {snap.buys_m5} compras / {snap.sells_m5} ventas, "
            f"{snap.change_m5:+.1f}% | 1h: {snap.change_h1:+.1f}%"
        )
        print(f"  Veredicto: {ev.verdict.value}  puntaje {ev.score}")
        for r in ev.reasons:
            print(f"    - {r}")
        print(f"  RugCheck: {'OK' if safety.ok else 'RECHAZADO'} (riesgo {safety.score})")
        for r in safety.reasons:
            print(f"    - {r}")


async def _balance(cfg) -> None:
    from .solana import SolanaRPC, load_keypair

    if not cfg.private_key:
        print("Falta SOLANA_PRIVATE_KEY en .env")
        return
    kp = load_keypair(cfg.private_key)
    async with aiohttp.ClientSession(trust_env=True) as session:
        sol = await SolanaRPC(session, cfg.rpc_url).get_balance_sol(str(kp.pubkey()))
    print(f"Wallet {kp.pubkey()}: {sol:.4f} SOL")


def _life_lines(cfg, st: State) -> list[str]:
    from .survival import compute_instinct

    life = st.life
    if not cfg.survival.enabled:
        return ["  Instinto de supervivencia: desactivado (survival.enabled = false)"]
    if life is None:
        return ["  Vida: Marín todavia no nace (nace al arrancar el bot)"]
    if not life.born:
        return [f"  Vida {life.number}: revivida, nace al arrancar el bot"]
    inst = compute_instinct(life, st.positions.values(), cfg.survival, cfg.risk)
    if life.dead:
        return [f"  💀 Vida {life.number}: MUERTA. {life.death_reason}"]
    return [
        f"  Vida {life.number}: {inst.emoji} {inst.mood}, miedo {inst.fear:.0%}"
        f" | patrimonio {inst.equity:.4f} SOL (mejor {inst.peak:.4f})"
        f" | muere en {inst.death_line:.4f} SOL",
        f"    Con este miedo: apuesta {cfg.risk.trade_size_sol * inst.size_mult:.4f} SOL,"
        f" pide +{inst.score_bonus:.0f} de puntaje, maximo {inst.max_open} posicion(es)",
    ]


def _bot_running(cfg, mode: str) -> bool:
    import socket

    if State(cfg.data_dir, mode).bot_running():
        return True
    if not cfg.dashboard.enabled:
        return False
    try:
        with socket.create_connection(("127.0.0.1", cfg.dashboard.port), timeout=0.5):
            return True
    except OSError:
        return False


def _revive(cfg, mode: str, saldo: float | None) -> None:
    from .survival import Life

    if saldo is not None and saldo <= 0:
        print("❌ --saldo tiene que ser mayor que 0.")
        return
    if _bot_running(cfg, mode):
        print(
            "⚠️  Marín está corriendo. Usa el boton 'Darle otra vida' del panel, o cierra el "
            "bot y vuelve a correr este comando."
        )
        return
    st = State(cfg.data_dir, mode)
    life = st.life
    if life is None or not life.born:
        print(f"Marín todavia no nace en modo {mode}: nace sola al arrancar el bot.")
        return
    if not life.dead:
        print(f"Marín está viva (vida {life.number}), no hay nada que revivir.")
        return
    print(f"💀 Vida {life.number}: {life.death_reason}")
    if saldo is not None:
        if mode != "paper":
            print("❌ --saldo solo funciona en simulacion (en real el saldo es el de tu wallet).")
            return
        print(f"   El saldo simulado pasa de {st.paper_balance or 0:.4f} a {saldo:.4f} SOL.")
    if input(f"¿Darle la vida {life.number + 1}? Escribe SI: ").strip().upper() != "SI":
        print("Cancelado, sigue muerta.")
        return
    st.life = Life(number=life.number + 1)
    if saldo is not None:
        st.paper_balance = saldo
    st.save()
    print(f"✅ Marín revivió: vida {life.number + 1}. Nace al arrancar el bot (start.bat).")


def _report(cfg, mode: str) -> None:
    st = State(cfg.data_dir, mode)
    for line in _life_lines(cfg, st):
        print(line)
    closed = st.closed
    if not closed and not st.positions:
        print(f"Sin operaciones en modo {mode} todavia.")
        return
    wins = [p for p in closed if p.realized_pnl_sol > 0]
    total = sum(p.realized_pnl_sol for p in closed)
    spent = sum(p.sol_spent for p in closed)
    print(f"Modo {mode}: {len(closed)} operaciones cerradas, {len(st.positions)} abiertas")
    if closed:
        print(f"  Ganadoras: {len(wins)}/{len(closed)} ({100 * len(wins) / len(closed):.0f}%)")
        print(
            f"  PnL total: {total:+.4f} SOL sobre {spent:.4f} SOL invertidos "
            f"({100 * total / spent if spent else 0:+.1f}%)"
        )
        best = max(closed, key=lambda p: p.sol_received / p.sol_spent if p.sol_spent else 0)
        print(f"  Mejor: {best.symbol} {best.sol_received / best.sol_spent:.2f}x")
        from .dashboard import stage_stats

        print("  Por etapa:")
        for s in stage_stats(closed):
            print(
                f"    {s['label']:<20} {s['trades']:>3} trades  aciertos {s['wins']}/{s['trades']}"
                f"  PnL {s['pnl_sol']:+.4f} SOL ({s['roi_pct']:+.1f}%)  mejor {s['best_x']:.2f}x"
            )
        from .analysis import compare_winners_losers, mode_stats

        print("  Por forma de entrada:")
        for s in mode_stats(closed):
            print(
                f"    {s['label']:<20} {s['trades']:>3} trades  aciertos {s['wins']}/{s['trades']}"
                f"  PnL {s['pnl_sol']:+.4f} SOL ({s['roi_pct']:+.1f}%)  mejor {s['best_x']:.2f}x"
            )
        cmp = compare_winners_losers(closed)
        if cmp["wins"] + cmp["losses"]:
            print(
                f"  Ganadoras vs perdedoras al comprar (mediana; {cmp['wins']} ganadoras, "
                f"{cmp['losses']} perdedoras con datos):"
            )
            for r in cmp["rows"]:
                w = "—" if r["win"] is None else f"{r['win']:,}"
                lo = "—" if r["loss"] is None else f"{r['loss']:,}"
                print(f"    {r['label']:<26} ganadoras {w:>12}   perdedoras {lo:>12}")
        print("  Ultimas 15:")
        for p in closed[-15:]:
            print(
                f"    {p.symbol:<12} {p.realized_pnl_sol:+.4f} SOL  "
                f"{p.sol_received / p.sol_spent if p.sol_spent else 0:.2f}x  {p.close_reason}"
            )
    if st.paper_balance is not None and mode == "paper":
        print(f"  Saldo simulado: {st.paper_balance:.4f} SOL")


async def _safe_limits(cfg, config_path: Path) -> None:
    from .solana import SolanaRPC, load_keypair
    from .wallet_setup import TRADE_FEES_SOL, apply_limits, recommended_limits

    if not cfg.private_key:
        print("Primero mete una wallet: python -m memebot import-wallet")
        return
    address = str(load_keypair(cfg.private_key).pubkey())
    async with aiohttp.ClientSession(trust_env=True) as session:
        balance = await SolanaRPC(session, cfg.rpc_url).get_balance_sol(address)
    lim = recommended_limits(balance)
    r = cfg.risk
    print(f"Saldo de la wallet: {balance:.4f} SOL")
    print("Limites recomendados (ahora -> nuevo):")
    labels = {
        "trade_size_sol": "SOL por operacion",
        "max_open_positions": "Posiciones a la vez",
        "max_capital_sol": "Tope de capital del bot",
        "max_daily_loss_sol": "Perdida maxima por dia",
        "max_total_loss_sol": "Perdida maxima total",
        "min_sol_reserve": "Reserva que nunca se toca",
    }
    for key, label in labels.items():
        print(f"  {label:<27} {getattr(r, key)!s:>6} -> {lim[key]}")
    print(f"  (cada operacion paga ~{TRADE_FEES_SOL} SOL de comisiones)")
    if input("¿Guardar estos limites en config.json? Escribe SI: ").strip().upper() != "SI":
        print("Cancelado, no se cambio nada.")
        return
    apply_limits(config_path, lim)
    print(f"✅ Guardado en {config_path}. Reinicia el bot para que los use.")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="memebot", description="Bot de meme coins en Solana")
    ap.add_argument("--config", default="config.json", help="archivo de configuracion")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="arrancar el bot")
    run.add_argument("--live", action="store_true", help="operar con dinero REAL")
    run.add_argument("--yes", action="store_true", help="no pedir confirmacion en modo live")
    chk = sub.add_parser("check", help="analizar un token puntual")
    chk.add_argument("mint")
    sub.add_parser("new-wallet", help="crear una wallet nueva para el bot")
    sub.add_parser("balance", help="ver saldo de la wallet del bot")
    sub.add_parser("setup-wallet", help="crear la wallet del bot y guardarla en .env")
    sub.add_parser("import-wallet", help="usar tu propia wallet (GMGN/Phantom) para el bot")
    est = sub.add_parser("estrategia", help="aplicar la estrategia recomendada (marin-v3)")
    est.add_argument("preset", nargs="?", default="marin-v3")
    sub.add_parser("safe-limits", help="poner limites de riesgo seguros segun tu saldo")
    st = sub.add_parser("set", help="cambiar un ajuste, ej: set risk.max_open_positions 4")
    st.add_argument("key")
    st.add_argument("value")
    sub.add_parser("preflight", help="revisar que todo este listo para real (sin operar)")
    rep = sub.add_parser("report", help="resumen de resultados")
    rep.add_argument("--live", action="store_true", help="ver resultados del modo live")
    rev = sub.add_parser("revivir", help="darle otra vida a Marín despues de morir")
    rev.add_argument("--live", action="store_true", help="revivir la del modo real")
    rev.add_argument("--saldo", type=float, help="(simulacion) empezar con este saldo en SOL")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # consola de Windows: permitir emojis/acentos
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.cmd == "setup-wallet":
        from .wallet_setup import setup_wallet

        address, created = setup_wallet(Path(".env"))
        if created:
            print("✅ Wallet del bot creada y guardada en el archivo .env")
        else:
            print("ℹ️  Ya tenias una wallet del bot en .env (no se cambio nada)")
        print(f"   Direccion: {address}")
        print(f"   Verla en GMGN: https://gmgn.ai/sol/address/{address}")
        print("   - NO deposites nada todavia: el bot sigue en simulacion.")
        print("   - Respaldo: abre .env con el Bloc de notas, copia la clave e importala en")
        print("     Phantom (Agregar cuenta -> Importar clave privada). Si pierdes .env sin")
        print("     respaldo, pierdes lo que haya en esa wallet.")
        print("   - Nunca compartas la clave con nadie, ni con Claude.")
        return

    if args.cmd == "import-wallet":
        import getpass

        from .wallet_setup import import_wallet, read_env_key

        print("Vas a usar TU wallet para el bot. El bot sigue en simulacion hasta que uses --live.")
        print("Pega tu clave privada y presiona Enter (no se vera nada al pegar, es normal).")
        print("En PowerShell se pega con clic derecho.")
        secret = getpass.getpass("Clave privada: ").strip()
        if not secret:
            print("Cancelado.")
            return
        from .solana import load_keypair

        try:
            address = str(load_keypair(secret).pubkey())
        except Exception:  # noqa: BLE001
            print("❌ Esa clave no es valida. Revisa que la copiaste completa.")
            return
        print(f"Direccion de esa clave: {address}")
        if read_env_key(Path(".env")):
            print("⚠️  Ya hay otra wallet en .env y se va a reemplazar.")
            print("   Si le depositaste algo, respaldala antes.")
        if input("¿Es la misma direccion que ves en GMGN? Escribe SI: ").strip().upper() != "SI":
            print("Cancelado, no se guardo nada.")
            return
        import_wallet(Path(".env"), secret)
        print("✅ Guardada en .env. Ahora corre: python -m memebot preflight")
        print("   Recomendado: pon risk.max_capital_sol en config.json para limitar cuanto usa.")
        return

    if args.cmd == "new-wallet":
        from solders.keypair import Keypair

        kp = Keypair()
        print("Wallet nueva creada. GUARDA la clave privada en un lugar seguro.")
        print(f"  Direccion (para depositar SOL): {kp.pubkey()}")
        print(f"  Clave privada (va en .env como SOLANA_PRIVATE_KEY): {kp}")
        print("Puedes importarla en Phantom para verla. Nunca la compartas con nadie.")
        return

    if args.cmd == "estrategia":
        from .config import STRATEGY_PRESETS, apply_preset, preset_changes

        if args.preset not in STRATEGY_PRESETS:
            print(f"❌ No existe '{args.preset}'. Opciones: {', '.join(STRATEGY_PRESETS)}")
            return
        path = Path(args.config)
        changes = preset_changes(path, args.preset)
        if not changes:
            print(f"✅ Ya tienes la estrategia {args.preset}, no hay nada que cambiar.")
            return
        print(f"Estrategia {args.preset} — cambios:")
        for key, old, new in changes:
            print(f"  {key:<42} {old!s:>10} -> {new}")
        if input("¿Aplicar? Escribe SI: ").strip().upper() != "SI":
            print("Cancelado, no se cambio nada.")
            return
        apply_preset(path, args.preset)
        print("✅ Listo. Reinicia el bot para que la use.")
        return

    if args.cmd == "set":
        from .config import set_setting

        try:
            old, new = set_setting(Path(args.config), args.key, args.value)
        except ValueError as e:
            print(f"❌ No se cambio nada: {e}")
            return
        print(f"✅ {args.key}: {old} -> {new}  (reinicia el bot para que lo use)")
        return

    cfg_path = args.config if Path(args.config).exists() else None
    cfg = load_config(cfg_path)
    if args.cmd == "run" and args.live:
        cfg.live = True
        from .config import validate

        validate(cfg)

    _setup_logging(cfg.log_level, cfg.data_dir)
    if cfg_path is None:
        logging.getLogger("memebot").warning(
            "No se encontro %s; usando valores por defecto (copia config.example.json)", args.config
        )

    if args.cmd == "run":
        if cfg.live and not args.yes:
            print("⚠️  MODO LIVE: el bot va a operar con DINERO REAL.")
            print(
                f"   Tamano por operacion: {cfg.risk.trade_size_sol} SOL | "
                f"max posiciones: {cfg.risk.max_open_positions} | "
                f"perdida diaria max: {cfg.risk.max_daily_loss_sol} SOL"
            )
            if input("Escribe SI para continuar: ").strip().upper() != "SI":
                print("Cancelado.")
                return
        try:
            asyncio.run(_run(cfg))
        except KeyboardInterrupt:
            print(
                "\nBot detenido. Las posiciones abiertas quedan guardadas y se retoman al reiniciar"
            )
    elif args.cmd == "check":
        asyncio.run(_check(cfg, args.mint))
    elif args.cmd == "balance":
        asyncio.run(_balance(cfg))
    elif args.cmd == "safe-limits":
        asyncio.run(_safe_limits(cfg, Path(args.config)))
    elif args.cmd == "preflight":
        from .wallet_setup import preflight

        icons = {"OK": "✅", "AVISO": "⚠️ ", "FALTA": "❌"}
        checks = asyncio.run(preflight(cfg))
        print("Revision para modo real (no se envio ninguna transaccion):")
        for status, msg in checks:
            print(f"  {icons[status]} {msg}")
        if any(s == "FALTA" for s, _ in checks):
            print("Resultado: todavia faltan cosas (marcadas con ❌).")
        elif any(s == "AVISO" for s, _ in checks):
            print("Resultado: tecnicamente listo, pero revisa los avisos ⚠️ .")
        else:
            print(
                "Resultado: todo listo. Aun asi, solo pasa a real cuando la simulacion sea buena."
            )
    elif args.cmd == "report":
        _report(cfg, "live" if args.live else "paper")
    elif args.cmd == "revivir":
        _revive(cfg, "live" if args.live else "paper", args.saldo)


if __name__ == "__main__":
    main()
