from kis_scanner.models import StockSnapshot
from kis_scanner.scoring import momentum_score, select_candidates


def stock(
    code: str,
    *,
    change: float,
    volume_rate: float,
    trade_value: int,
    rank: int = 1,
) -> StockSnapshot:
    return StockSnapshot(
        code=code,
        name=f"기업{code}",
        data_rank=rank,
        price=10_000,
        change_rate=change,
        accumulated_volume=100_000,
        accumulated_trade_value=trade_value,
        volume_increase_rate=volume_rate,
    )


def test_stronger_momentum_receives_higher_score() -> None:
    weak = stock("000001", change=2.0, volume_rate=80.0, trade_value=2_000_000_000)
    strong = stock("000002", change=7.0, volume_rate=350.0, trade_value=15_000_000_000)
    assert momentum_score(strong) > momentum_score(weak)


def test_candidate_filter_removes_low_liquidity_and_overheated_stock() -> None:
    stocks = [
        stock("000001", change=5.0, volume_rate=300.0, trade_value=5_000_000_000),
        stock("000002", change=5.0, volume_rate=300.0, trade_value=100_000_000),
        stock("000003", change=25.0, volume_rate=300.0, trade_value=5_000_000_000),
    ]
    results = select_candidates(stocks)
    assert [item.snapshot.code for item in results] == ["000001"]

