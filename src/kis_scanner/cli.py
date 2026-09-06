from __future__ import annotations

import argparse
import logging
import sys

import requests

from .client import KisApiError, KisClient
from .config import Settings
from .models import ScoredStock
from .scanner import MarketScanner
from .storage import PriceObservationRecord, SignalRecord, SignalStore
from .tracker import SignalTracker, TrackError


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
    scan.add_argument("--save", action="store_true", help="조회 결과를 SQLite DB에 저장")

    track = subparsers.add_parser("track", help="최근 저장 신호의 현재가를 조회해 가격 변화를 저장")
    track.add_argument("--hours", type=float, default=24.0, help="추적할 최근 신호 시간 범위")
    track.add_argument("--delay", type=float, default=0.3, help="종목별 현재가 조회 사이 대기 초")

    history = subparsers.add_parser("history", help="최근 저장된 신호를 출력")
    history.add_argument("--limit", type=int, default=20)
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


def _print_signals(signals: list[SignalRecord]) -> None:
    if not signals:
        print("저장된 신호가 없습니다.")
        return

    header = f"{'id':>5} {'detected_at':<25} {'code':<6} {'name':<16} {'price':>10} {'score':>7}"
    print(header)
    print("-" * len(header))
    for signal in signals:
        print(
            f"{signal.id:>5} {signal.detected_at:<25} {signal.code:<6} "
            f"{signal.name:<16.16} {signal.price:>10,} {signal.score:>7.2f}"
        )


def _print_observations(records: list[PriceObservationRecord]) -> None:
    if not records:
        print("저장된 가격 관측이 없습니다.")
        return

    header = (
        f"{'signal':>6} {'code':<6} {'name':<16} {'signal_px':>10} "
        f"{'now_px':>10} {'return%':>8} {'mins':>7}"
    )
    print(header)
    print("-" * len(header))
    for record in records:
        print(
            f"{record.signal_id:>6} {record.code:<6} {record.name:<16.16} "
            f"{record.signal_price:>10,} {record.observed_price:>10,} "
            f"{record.return_pct:>8.2f} {record.minutes_elapsed:>7.1f}"
        )


def _print_track_errors(errors: list[TrackError]) -> None:
    if not errors:
        return
    print(f"추적 실패 {len(errors)}건:")
    for error in errors:
        print(f"- signal {error.signal.id} {error.signal.code} {error.signal.name}: {error.message}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parser().parse_args()
    try:
        settings = Settings.from_env()
        client = KisClient(settings)
        store = SignalStore(settings.database_path)

        if args.command == "check":
            count = client.check_connection()
            print(f"연결 성공: 거래량 순위 {count}건 수신")
            return

        if args.command == "history":
            _print_signals(store.latest_signals(limit=args.limit))
            return

        if args.command == "track":
            result = SignalTracker(client, store).track_recent(
                hours=args.hours, delay_seconds=args.delay
            )
            _print_observations(result.records)
            _print_track_errors(result.errors)
            print(f"가격 관측 {len(result.records)}건 저장: {settings.database_path}")
            return

        results = MarketScanner(client).scan(
            min_change_rate=args.min_change,
            max_change_rate=args.max_change,
            min_trade_value_krw=args.min_value_krw,
            top_n=args.top,
        )
        _print_results(results)
        if args.save:
            saved_count = store.save_signals(results)
            print(f"신호 {saved_count}건 저장: {settings.database_path}")
    except (ValueError, KisApiError, requests.RequestException) as exc:
        logging.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
