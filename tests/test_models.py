from kis_scanner.models import StockSnapshot


def test_snapshot_parses_kis_strings() -> None:
    snapshot = StockSnapshot.from_kis(
        {
            "mksc_shrn_iscd": "123456",
            "hts_kor_isnm": "테스트기업",
            "data_rank": "2",
            "stck_prpr": "8,420",
            "prdy_ctrt": "4.80",
            "acml_vol": "2500000",
            "acml_tr_pbmn": "12800000000",
            "vol_inrt": "365.2",
        }
    )

    assert snapshot.code == "123456"
    assert snapshot.price == 8420
    assert snapshot.change_rate == 4.8
    assert snapshot.accumulated_trade_value == 12_800_000_000


def test_snapshot_tolerates_missing_numeric_fields() -> None:
    snapshot = StockSnapshot.from_kis(
        {"mksc_shrn_iscd": "123456", "hts_kor_isnm": "테스트", "stck_prpr": "1000"}
    )
    assert snapshot.accumulated_trade_value == 0
    assert snapshot.volume_increase_rate == 0.0

