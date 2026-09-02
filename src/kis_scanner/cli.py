from __future__ import annotations

import argparse
import logging
import sys

import requests

from .client import KisApiError, KisClient
from .config import Settings
from .models import ScoredStock
from .scanner import MarketScanner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="한국투자 Open API 기반 국내주식 이상징후 탐지 MVP"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="인증과 거래량 순위 API 연결 확인")

    scan = subparsers.add_parser("scan", help="후보 종목을 점수순으로 출력")
    scan.add_argument("--top", type=int, default=10)
    scan.add_argument("--min-change", type=float, default=1.0)
    scan.add_argument("--max-change", type=float, default=20.0)
    scan.add_argument("--min-value-krw", type=int, default=1_000_000_000)
    return parser


def _print_results(results: list[ScoredStock]) -> None:
    if not results:
        print("조건을 만족한 후보가 없습니다.")
        return

    header = (
        f"{'rank':>4} {'code':<6} {'name':<16} {'price':>10} "
        f"{'change%':>8} {'volume%':>9} {'trade_value':>17} {'score':>7}"
    )
    print(header)
    print("-" * len(header))
    for index, item in enumerate(results, start=1):
        stock = item.snapshot
        print(
            f"{index:>4} {stock.code:<6} {stock.name:<16.16} "
            f"{stock.price:>10,} {stock.change_rate:>8.2f} "
            f"{stock.volume_increase_rate:>9.2f} "
            f"{stock.accumulated_trade_value:>17,} {item.score:>7.2f}"
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parser().parse_args()
    try:
        settings = Settings.from_env()
        client = KisClient(settings)

        if args.command == "check":
            count = client.check_connection()
            print(f"연결 성공: 거래량 순위 {count}건 수신")
            return

        results = MarketScanner(client).scan(
            min_change_rate=args.min_change,
            max_change_rate=args.max_change,
            min_trade_value_krw=args.min_value_krw,
            top_n=args.top,
        )
        _print_results(results)
    except (ValueError, KisApiError, requests.RequestException) as exc:
        logging.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
