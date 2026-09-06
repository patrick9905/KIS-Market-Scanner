from __future__ import annotations

from dataclasses import dataclass
from time import sleep

from .client import KisApiError, KisClient
from .storage import PriceObservationRecord, SignalRecord, SignalStore


@dataclass(frozen=True)
class TrackError:
    signal: SignalRecord
    message: str


@dataclass(frozen=True)
class TrackResult:
    records: list[PriceObservationRecord]
    errors: list[TrackError]


class SignalTracker:
    def __init__(self, client: KisClient, store: SignalStore) -> None:
        self.client = client
        self.store = store

    def track_recent(self, *, hours: float = 24.0, delay_seconds: float = 0.3) -> TrackResult:
        signals = self.store.signals_since(hours=hours)
        if not signals:
            return TrackResult(records=[], errors=[])

        records: list[PriceObservationRecord] = []
        errors: list[TrackError] = []
        snapshot_by_code = {}

        for index, code in enumerate(sorted({signal.code for signal in signals})):
            if index > 0 and delay_seconds > 0:
                sleep(delay_seconds)
            try:
                snapshot_by_code[code] = self.client.get_current_price(code)
            except KisApiError as exc:
                for signal in signals:
                    if signal.code == code:
                        errors.append(TrackError(signal=signal, message=str(exc)))

        for signal in signals:
            snapshot = snapshot_by_code.get(signal.code)
            if snapshot is None:
                continue
            records.append(self.store.save_price_observation(signal, snapshot))

        return TrackResult(records=records, errors=errors)
