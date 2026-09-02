from __future__ import annotations

import math

from .models import ScoredStock, StockSnapshot


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def momentum_score(stock: StockSnapshot) -> float:
    """V1 가설 점수.

    이 함수의 가중치는 수익성이 입증된 값이 아니다. 신호 결과를 축적한 뒤
    walk-forward 검증으로 수정하기 위해 한곳에 명시적으로 모아 둔다.
    """

    change_component = 40.0 * _clamp(stock.change_rate / 10.0)
    volume_component = 35.0 * _clamp(stock.volume_increase_rate / 300.0)

    # 10억원을 0점 부근, 100억원 이상을 만점 부근으로 보는 로그 스케일.
    value_billions = stock.accumulated_trade_value / 1_000_000_000
    value_component = 20.0 * _clamp(
        math.log10(max(value_billions, 1.0)) / 1.0
    )

    rank_component = 5.0 * _clamp((31 - max(stock.data_rank, 1)) / 30.0)
    return round(change_component + volume_component + value_component + rank_component, 2)


def select_candidates(
    stocks: list[StockSnapshot],
    *,
    min_change_rate: float = 1.0,
    max_change_rate: float = 20.0,
    min_trade_value_krw: int = 1_000_000_000,
    top_n: int = 10,
) -> list[ScoredStock]:
    candidates = [
        stock
        for stock in stocks
        if stock.is_valid()
        and min_change_rate <= stock.change_rate <= max_change_rate
        and stock.accumulated_trade_value >= min_trade_value_krw
    ]
    scored = [ScoredStock(stock, momentum_score(stock)) for stock in candidates]
    return sorted(scored, key=lambda item: item.score, reverse=True)[:top_n]

