from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _number(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    return int(_number(value, float(default)))


@dataclass(frozen=True)
class StockSnapshot:
    code: str
    name: str
    data_rank: int
    price: int
    change_rate: float
    accumulated_volume: int
    accumulated_trade_value: int
    volume_increase_rate: float

    @classmethod
    def from_kis(cls, row: Mapping[str, Any]) -> "StockSnapshot":
        return cls(
            code=str(row.get("mksc_shrn_iscd", "")).strip(),
            name=str(row.get("hts_kor_isnm", "")).strip(),
            data_rank=_integer(row.get("data_rank")),
            price=_integer(row.get("stck_prpr")),
            change_rate=_number(row.get("prdy_ctrt")),
            accumulated_volume=_integer(row.get("acml_vol")),
            accumulated_trade_value=_integer(row.get("acml_tr_pbmn")),
            volume_increase_rate=_number(row.get("vol_inrt")),
        )

    def is_valid(self) -> bool:
        return bool(self.code and self.name and self.price > 0)


@dataclass(frozen=True)
class PriceSnapshot:
    code: str
    price: int
    change_rate: float
    accumulated_volume: int
    accumulated_trade_value: int

    @classmethod
    def from_kis(cls, code: str, row: Mapping[str, Any]) -> "PriceSnapshot":
        return cls(
            code=code,
            price=_integer(row.get("stck_prpr")),
            change_rate=_number(row.get("prdy_ctrt")),
            accumulated_volume=_integer(row.get("acml_vol")),
            accumulated_trade_value=_integer(row.get("acml_tr_pbmn")),
        )

    def is_valid(self) -> bool:
        return bool(self.code and self.price > 0)


@dataclass(frozen=True)
class ScoredStock:
    snapshot: StockSnapshot
    score: float
