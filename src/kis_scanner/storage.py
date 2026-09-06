from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import PriceSnapshot, ScoredStock


@dataclass(frozen=True)
class SignalRecord:
    id: int
    detected_at: str
    code: str
    name: str
    price: int
    score: float


@dataclass(frozen=True)
class PriceObservationRecord:
    signal_id: int
    code: str
    name: str
    signal_price: int
    observed_price: int
    return_pct: float
    minutes_elapsed: float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SignalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def ensure_schema(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    detected_at TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    data_rank INTEGER NOT NULL,
                    price INTEGER NOT NULL,
                    change_rate REAL NOT NULL,
                    accumulated_volume INTEGER NOT NULL,
                    accumulated_trade_value INTEGER NOT NULL,
                    volume_increase_rate REAL NOT NULL,
                    score REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS price_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    code TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    change_rate REAL NOT NULL,
                    accumulated_volume INTEGER NOT NULL,
                    accumulated_trade_value INTEGER NOT NULL,
                    return_from_signal_pct REAL NOT NULL,
                    minutes_elapsed REAL NOT NULL,
                    FOREIGN KEY (signal_id) REFERENCES signals(id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_signals_detected_at ON signals(detected_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_signals_code ON signals(code)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_observations_signal ON price_observations(signal_id)"
            )

    def save_signals(
        self,
        results: Iterable[ScoredStock],
        *,
        detected_at: str | None = None,
    ) -> int:
        detected_at = detected_at or utc_now_iso()
        rows = []
        for item in results:
            stock = item.snapshot
            rows.append(
                (
                    detected_at,
                    stock.code,
                    stock.name,
                    stock.data_rank,
                    stock.price,
                    stock.change_rate,
                    stock.accumulated_volume,
                    stock.accumulated_trade_value,
                    stock.volume_increase_rate,
                    item.score,
                )
            )
        if not rows:
            return 0

        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO signals (
                    detected_at, code, name, data_rank, price, change_rate,
                    accumulated_volume, accumulated_trade_value,
                    volume_increase_rate, score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def latest_signals(self, *, limit: int = 20) -> list[SignalRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, detected_at, code, name, price, score
                FROM signals
                ORDER BY detected_at DESC, score DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._signal_from_row(row) for row in rows]

    def signals_since(self, *, hours: float = 24.0) -> list[SignalRecord]:
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        records = self.latest_signals(limit=10_000)
        return [
            record
            for record in records
            if datetime.fromisoformat(record.detected_at).timestamp() >= cutoff
        ]

    def save_price_observation(
        self,
        signal: SignalRecord,
        snapshot: PriceSnapshot,
        *,
        observed_at: str | None = None,
    ) -> PriceObservationRecord:
        observed_at = observed_at or utc_now_iso()
        detected = datetime.fromisoformat(signal.detected_at)
        observed = datetime.fromisoformat(observed_at)
        minutes_elapsed = (observed - detected).total_seconds() / 60
        return_pct = ((snapshot.price - signal.price) / signal.price) * 100

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO price_observations (
                    signal_id, observed_at, code, price, change_rate,
                    accumulated_volume, accumulated_trade_value,
                    return_from_signal_pct, minutes_elapsed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.id,
                    observed_at,
                    snapshot.code,
                    snapshot.price,
                    snapshot.change_rate,
                    snapshot.accumulated_volume,
                    snapshot.accumulated_trade_value,
                    return_pct,
                    minutes_elapsed,
                ),
            )
        return PriceObservationRecord(
            signal_id=signal.id,
            code=signal.code,
            name=signal.name,
            signal_price=signal.price,
            observed_price=snapshot.price,
            return_pct=round(return_pct, 2),
            minutes_elapsed=round(minutes_elapsed, 1),
        )

    @staticmethod
    def _signal_from_row(row: sqlite3.Row) -> SignalRecord:
        return SignalRecord(
            id=int(row["id"]),
            detected_at=str(row["detected_at"]),
            code=str(row["code"]),
            name=str(row["name"]),
            price=int(row["price"]),
            score=float(row["score"]),
        )
