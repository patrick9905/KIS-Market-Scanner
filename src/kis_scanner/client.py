from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .models import PriceSnapshot
from .strategy import Bar


class KisApiError(RuntimeError):
    """한국투자 API가 정상 결과를 반환하지 않았을 때 발생합니다."""


class KisClient:
    TOKEN_PATH = "/oauth2/tokenP"
    VOLUME_RANK_PATH = "/uapi/domestic-stock/v1/quotations/volume-rank"
    VOLUME_RANK_TR_ID = "FHPST01710000"
    CURRENT_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
    CURRENT_PRICE_TR_ID = "FHKST01010100"

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def get_access_token(self) -> str:
        cached = self._read_cached_token()
        if cached:
            return cached

        response = self.session.post(
            f"{self.settings.base_url}{self.TOKEN_PATH}",
            headers={"content-type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.settings.app_key,
                "appsecret": self.settings.app_secret,
            },
            timeout=self.settings.request_timeout_seconds,
        )
        payload = self._decode_response(response, action="접근 토큰 발급")
        token = str(payload.get("access_token", "")).strip()
        if not token:
            raise KisApiError("접근 토큰 응답에 access_token이 없습니다.")

        expires_in = int(payload.get("expires_in", 86_400))
        self._write_cached_token(token, expires_in)
        return token

    def get_volume_rank(self) -> list[dict[str, Any]]:
        token = self.get_access_token()
        response = self.session.get(
            f"{self.settings.base_url}{self.VOLUME_RANK_PATH}",
            headers=self._auth_headers(token, self.VOLUME_RANK_TR_ID),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": self.settings.market_code,
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "1",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": self.settings.exclude_code,
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "1000000",
                "FID_VOL_CNT": "10000",
                "FID_INPUT_DATE_1": "",
            },
            timeout=self.settings.request_timeout_seconds,
        )
        payload = self._decode_response(response, action="거래량 순위 조회")
        self._raise_for_kis_error(payload, action="거래량 순위 조회")
        output = payload.get("output") or []
        if not isinstance(output, list):
            raise KisApiError("거래량 순위 output 형식이 list가 아닙니다.")
        return output

    def get_current_price(self, code: str) -> PriceSnapshot:
        token = self.get_access_token()
        response = self.session.get(
            f"{self.settings.base_url}{self.CURRENT_PRICE_PATH}",
            headers=self._auth_headers(token, self.CURRENT_PRICE_TR_ID),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
            },
            timeout=self.settings.request_timeout_seconds,
        )
        payload = self._decode_response(response, action=f"현재가 조회({code})")
        self._raise_for_kis_error(payload, action=f"현재가 조회({code})")
        output = payload.get("output") or {}
        if not isinstance(output, dict):
            raise KisApiError("현재가 output 형식이 dict가 아닙니다.")
        snapshot = PriceSnapshot.from_kis(code, output)
        if not snapshot.is_valid():
            raise KisApiError(f"현재가 조회({code}) 응답에 유효한 가격이 없습니다.")
        return snapshot

    def check_connection(self) -> int:
        return len(self.get_volume_rank())

    def get_daily_bars(self, code: str, start: str, end: str) -> list[Bar]:
        response = self.session.get(
            f'{self.settings.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
            headers=self._auth_headers(self.get_access_token(), 'FHKST03010100'),
            params={'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': code,
                    'FID_INPUT_DATE_1': start.replace('-', ''),
                    'FID_INPUT_DATE_2': end.replace('-', ''),
                    'FID_PERIOD_DIV_CODE': 'D', 'FID_ORG_ADJ_PRC': '0'},
            timeout=self.settings.request_timeout_seconds,
        )
        payload = self._decode_response(response, action='daily bars')
        self._raise_for_kis_error(payload, action='daily bars')
        rows = payload.get('output2')
        if not isinstance(rows, list):
            raise KisApiError('Missing daily bar output2')
        bars = []
        for row in rows:
            if not isinstance(row, dict):
                raise KisApiError('Invalid daily bar row')
            if not row or not row.get('stck_bsop_date'):
                continue
            day = datetime.strptime(row['stck_bsop_date'], '%Y%m%d').date().isoformat()
            bars.append(Bar(day, float(row['stck_oprc']), float(row['stck_hgpr']),
                            float(row['stck_lwpr']), float(row['stck_clpr']), int(row['acml_vol'])))
        bars.sort(key=lambda b: b.day)
        if len({b.day for b in bars}) != len(bars):
            raise KisApiError('Duplicate daily bars')
        return bars

    def _auth_headers(self, token: str, tr_id: str) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _read_cached_token(self) -> str | None:
        path = self.settings.token_cache_path
        if not path.exists():
            return None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(cached["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > datetime.now(timezone.utc) + timedelta(minutes=5):
                return str(cached["access_token"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            return None
        return None

    def _write_cached_token(self, token: str, expires_in: int) -> None:
        path: Path = self.settings.token_cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        path.write_text(
            json.dumps(
                {"access_token": token, "expires_at": expires_at.isoformat()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _decode_response(response: requests.Response, *, action: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise KisApiError(
                f"{action} 실패: HTTP {response.status_code}, JSON이 아닌 응답"
            ) from exc

        if not isinstance(payload, dict):
            raise KisApiError(f'{action}: expected a JSON object')
        if not response.ok:
            message = payload.get("msg1") or payload.get("error_description") or payload
            raise KisApiError(f"{action} 실패: HTTP {response.status_code}, {message}")
        return payload

    @staticmethod
    def _raise_for_kis_error(payload: dict[str, Any], *, action: str) -> None:
        if payload.get("rt_cd") not in (None, "0"):
            code = payload.get("msg_cd", "UNKNOWN")
            message = payload.get("msg1", "알 수 없는 오류")
            raise KisApiError(f"{action} 실패: {code} {message}")
