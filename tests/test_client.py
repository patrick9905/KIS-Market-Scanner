from pathlib import Path

import pytest

from kis_scanner.client import KisApiError, KisClient
from kis_scanner.config import Settings


class FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 400

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.post_calls = 0
        self.last_params = None
        self.last_tr_id = None

    def post(self, *_args, **_kwargs) -> FakeResponse:
        self.post_calls += 1
        return FakeResponse({"access_token": "token", "expires_in": 3600})

    def get(self, *_args, **kwargs) -> FakeResponse:
        self.last_params = kwargs["params"]
        self.last_tr_id = kwargs["headers"]["tr_id"]
        if self.last_tr_id == KisClient.CURRENT_PRICE_TR_ID:
            return FakeResponse(
                {
                    "rt_cd": "0",
                    "output": {
                        "stck_prpr": "11000",
                        "prdy_ctrt": "3.50",
                        "acml_vol": "100000",
                        "acml_tr_pbmn": "1100000000",
                    },
                }
            )
        return FakeResponse(
            {
                "rt_cd": "0",
                "output": [{"mksc_shrn_iscd": "123456", "hts_kor_isnm": "테스트"}],
            }
        )


def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_key="app-key",
        app_secret="app-secret",
        token_cache_path=tmp_path / "token.json",
    )


def test_token_is_cached_and_reused(tmp_path: Path) -> None:
    session = FakeSession()
    client = KisClient(settings(tmp_path), session=session)
    assert client.get_access_token() == "token"
    assert client.get_access_token() == "token"
    assert session.post_calls == 1


def test_volume_rank_uses_configured_market(tmp_path: Path) -> None:
    session = FakeSession()
    client = KisClient(settings(tmp_path), session=session)
    rows = client.get_volume_rank()
    assert len(rows) == 1
    assert session.last_params["FID_INPUT_ISCD"] == "1001"


def test_current_price_uses_stock_code(tmp_path: Path) -> None:
    session = FakeSession()
    client = KisClient(settings(tmp_path), session=session)
    snapshot = client.get_current_price("005930")
    assert snapshot.code == "005930"
    assert snapshot.price == 11_000
    assert session.last_tr_id == KisClient.CURRENT_PRICE_TR_ID
    assert session.last_params["FID_INPUT_ISCD"] == "005930"


def test_kis_error_is_not_silently_ignored() -> None:
    with pytest.raises(KisApiError, match="EGW00201"):
        KisClient._raise_for_kis_error(
            {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"},
            action="조회",
        )
