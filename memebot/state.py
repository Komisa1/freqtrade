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
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


class State:
    def __init__(self, data_dir: str | Path, mode: str):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"state_{mode}.json"
        self.trades_path = self.dir / f"trades_{mode}.csv"
        self.positions: dict[str, Position] = {}
        self.closed: list[Position] = []
        self.traded_mints: set[str] = set()
        self.last_loss_time: float = 0.0
        self.paper_balance: float | None = None
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

    def save(self) -> None:
        data = {
            "open": [asdict(p) for p in self.positions.values()],
            "closed": [asdict(p) for p in self.closed[-2000:]],
            "traded_mints": sorted(self.traded_mints),
            "last_loss_time": self.last_loss_time,
            "paper_balance": self.paper_balance,
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
