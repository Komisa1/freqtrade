"""Persistencia en disco: posiciones abiertas/cerradas, tokens ya operados y log de trades."""

from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import Position


def _day(ts: float) -> str:
    # dia en la hora local de tu compu (igual que el calendario del panel)
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class State:
    def __init__(self, data_dir: str | Path, mode: str):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"state_{mode}.json"
        self.trades_path = self.dir / f"trades_{mode}.csv"
        self.equity_path = self.dir / f"equity_{mode}.csv"
        self.prices_dir = self.dir / f"prices_{mode}"
        self.positions: dict[str, Position] = {}
        self.closed: list[Position] = []
        self.traded_mints: set[str] = set()
        self.last_loss_time: float = 0.0
        self.paper_balance: float | None = None
        # etapas en las que el bot tiene permitido comprar (se cambia desde el panel)
        self.buy_stages: list[str] = ["new", "about", "migrated"]
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.positions = {p["mint"]: Position(**p) for p in data.get("open", [])}
        self.closed = [Position(**p) for p in data.get("closed", [])]
        self.traded_mints = set(data.get("traded_mints", []))
        self.last_loss_time = data.get("last_loss_time", 0.0)
        self.paper_balance = data.get("paper_balance")
        self.buy_stages = data.get("buy_stages", self.buy_stages)

    def save(self) -> None:
        data = {
            "open": [asdict(p) for p in self.positions.values()],
            "closed": [asdict(p) for p in self.closed[-2000:]],
            "traded_mints": sorted(self.traded_mints),
            "last_loss_time": self.last_loss_time,
            "paper_balance": self.paper_balance,
            "buy_stages": self.buy_stages,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def open_position(self, pos: Position) -> None:
        self.positions[pos.mint] = pos
        self.traded_mints.add(pos.mint)
        self.save()

    def close_position(self, pos: Position, reason: str) -> None:
        pos.closed = True
        pos.close_time = time.time()
        pos.close_reason = reason
        self.positions.pop(pos.mint, None)
        self.closed.append(pos)
        if pos.realized_pnl_sol < 0:
            self.last_loss_time = pos.close_time
        self.save()

    def realized_pnl_today(self, now: float | None = None) -> float:
        today = _day(now or time.time())
        return sum(p.realized_pnl_sol for p in self.closed if _day(p.close_time) == today)

    def record_price(self, mint: str, t: float, price: float) -> None:
        """Historial de precio de cada posicion (para dibujar la grafica con tus compras)."""
        self.prices_dir.mkdir(exist_ok=True)
        with (self.prices_dir / f"{mint}.csv").open("a", encoding="utf-8") as f:
            f.write(f"{t:.0f},{price:.12g}\n")

    def load_prices(self, mint: str) -> list[tuple[float, float]]:
        path = self.prices_dir / f"{mint}.csv"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                t, p = line.split(",")
                out.append((float(t), float(p)))
            except ValueError:
                continue
        return out

    def trades_for(self, mint: str) -> list[dict]:
        """Compras y ventas de un token, leidas del registro trades_*.csv."""
        if not self.trades_path.exists():
            return []
        out = []
        with self.trades_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("mint") != mint:
                    continue
                try:
                    out.append(
                        {
                            "t": datetime.fromisoformat(row["time_utc"]).timestamp(),
                            "side": row["side"].lower(),
                            "sol": float(row["sol"]),
                            "price": float(row["price_usd"]),
                            "reason": row.get("reason", ""),
                        }
                    )
                except (KeyError, ValueError):
                    continue
        return out

    def append_equity(self, point: tuple[float, float]) -> None:
        with self.equity_path.open("a", encoding="utf-8") as f:
            f.write(f"{point[0]:.0f},{point[1]:.6f}\n")

    def load_equity(self, since: float = 0.0) -> list[tuple[float, float]]:
        if not self.equity_path.exists():
            return []
        out = []
        for line in self.equity_path.read_text(encoding="utf-8").splitlines():
            try:
                t, v = line.split(",")
                if float(t) >= since:
                    out.append((float(t), float(v)))
            except ValueError:
                continue
        return out

    def log_trade(
        self,
        side: str,
        pos: Position,
        sol: float,
        tokens: float,
        price: float,
        reason: str,
        signature: str,
    ) -> None:
        new = not self.trades_path.exists()
        with self.trades_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(
                    [
                        "time_utc",
                        "side",
                        "symbol",
                        "mint",
                        "sol",
                        "tokens",
                        "price_usd",
                        "multiple",
                        "reason",
                        "signature",
                    ]
                )
            w.writerow(
                [
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    side,
                    pos.symbol,
                    pos.mint,
                    f"{sol:.6f}",
                    f"{tokens:.4f}",
                    f"{price:.10g}",
                    f"{pos.multiple(price):.3f}",
                    reason,
                    signature,
                ]
            )
