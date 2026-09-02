from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    app_key: str
    app_secret: str
    base_url: str = "https://openapi.koreainvestment.com:9443"
    market_code: str = "1001"
    exclude_code: str = "000000"
    token_cache_path: Path = Path(".cache/kis-token.json")
    request_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "Settings":
        load_dotenv(env_file)
        app_key = os.getenv("KIS_APP_KEY", "").strip()
        app_secret = os.getenv("KIS_APP_SECRET", "").strip()

        if not app_key or not app_secret:
            raise ValueError(
                "KIS_APP_KEY와 KIS_APP_SECRET이 필요합니다. "
                ".env.example을 복사해 .env를 만든 뒤 값을 입력하세요."
            )

        return cls(
            app_key=app_key,
            app_secret=app_secret,
            base_url=os.getenv(
                "KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"
            ).rstrip("/"),
            market_code=os.getenv("KIS_MARKET_CODE", "1001").strip(),
            exclude_code=os.getenv("KIS_EXCLUDE_CODE", "000000").strip(),
        )

