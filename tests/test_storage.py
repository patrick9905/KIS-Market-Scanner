from kis_scanner.models import PriceSnapshot, ScoredStock, StockSnapshot
from kis_scanner.storage import SignalStore
from kis_scanner.tracker import SignalTracker


def scored_stock() -> ScoredStock:
    return ScoredStock(
        StockSnapshot(
            code="123456",
            name="테스트기업",
            data_rank=1,
            price=10_000,
            change_rate=5.0,
            accumulated_volume=100_000,
            accumulated_trade_value=1_500_000_000,
            volume_increase_rate=250.0,
        ),
        score=77.7,
    )


class FakeClient:
    def get_current_price(self, code: str) -> PriceSnapshot:
        return PriceSnapshot(
            code=code,
            price=10_500,
            change_rate=6.0,
            accumulated_volume=120_000,
            accumulated_trade_value=1_800_000_000,
        )


def test_store_saves_scan_signals(tmp_path) -> None:
    store = SignalStore(tmp_path / "signals.sqlite3")
    saved = store.save_signals(
        [scored_stock()], detected_at="2026-09-02T01:00:00+00:00"
    )

    signals = store.latest_signals()
    assert saved == 1
    assert len(signals) == 1
    assert signals[0].code == "123456"
    assert signals[0].price == 10_000


def test_tracker_records_return_from_signal_price(tmp_path) -> None:
    store = SignalStore(tmp_path / "signals.sqlite3")
    store.save_signals([scored_stock()])

    result = SignalTracker(FakeClient(), store).track_recent(hours=24, delay_seconds=0)

    assert result.errors == []
    assert len(result.records) == 1
    assert result.records[0].code == "123456"
    assert result.records[0].observed_price == 10_500
    assert result.records[0].return_pct == 5.0
