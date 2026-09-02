from __future__ import annotations

from .client import KisClient
from .models import ScoredStock, StockSnapshot
from .scoring import select_candidates


class MarketScanner:
    def __init__(self, client: KisClient) -> None:
        self.client = client

    def scan(
        self,
        *,
        min_change_rate: float = 1.0,
        max_change_rate: float = 20.0,
        min_trade_value_krw: int = 1_000_000_000,
        top_n: int = 10,
    ) -> list[ScoredStock]:
        rows = self.client.get_volume_rank()
        snapshots = [StockSnapshot.from_kis(row) for row in rows]
        return select_candidates(
            snapshots,
            min_change_rate=min_change_rate,
            max_change_rate=max_change_rate,
            min_trade_value_krw=min_trade_value_krw,
            top_n=top_n,
        )

