from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import asdict
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path

import requests

from .client import KisApiError, KisClient
from .config import Settings
from .paper import BotConfig, KST, PaperEngine
from .strategy import Bar

LOG = logging.getLogger(__name__)


def collect(client: KisClient, cfg: BotConfig, start: str, end: str) -> dict[str, list[Bar]]:
    result = {}
    for code in dict.fromkeys((*cfg.universe, cfg.benchmark)):
        cursor, rows = end, {}
        while cursor >= start:
            time.sleep(1)
            page = client.get_daily_bars(code, start, cursor)
            if not page:
                break
            if any(b.day > cursor or b.day < start for b in page):
                raise ValueError('API returned bars outside requested dates')
            rows.update({b.day: b for b in page})
            cursor = (date.fromisoformat(page[0].day) - timedelta(days=1)).isoformat()
            if len(page) < 100:
                break
        result[code] = sorted(rows.values(), key=lambda b: b.day)
    return result


def run_once(client: KisClient, engine: PaperEngine, kill_file: Path) -> dict:
    now = datetime.now(KST)
    if now.weekday() >= 5 or not clock_time(9) <= now.time() <= clock_time(15, 30):
        engine.event('idle', {'reason': 'outside_paper_session'})
        return {'status': 'idle', 'reason': 'outside_paper_session', 'at': now.isoformat()}
    start_clock = time.monotonic()
    day = now.date().isoformat()
    bars = collect(client, engine.config, (now.date() - timedelta(days=180)).isoformat(), day)
    # An actual same-day bar with volume is required, including on weekday holidays.
    if any(not rows or rows[-1].day != day or rows[-1].volume <= 0 for rows in bars.values()):
        engine.event('idle', {'reason': 'missing_current_session_bar'})
        return {'status': 'idle', 'reason': 'missing_current_session_bar'}
    histories = {c: [b for b in rows if b.day < day] for c, rows in bars.items()}
    if any(len(rows) < 61 for rows in histories.values()):
        raise ValueError('At least 61 completed daily bars required for every symbol')
    last_session = histories[engine.config.benchmark][-1].day
    if any(rows[-1].day != last_session for rows in histories.values()):
        raise ValueError('Completed history sessions do not match the benchmark')
    prices = {}
    for code in bars:
        time.sleep(1)
        quote = client.get_current_price(code)
        if quote.accumulated_volume < bars[code][-1].volume:
            raise ValueError('Quote volume predates collected daily bar')
        prices[code] = quote.price
    observed = datetime.now(KST)
    if time.monotonic() - start_clock > 60 or observed.date() != now.date() or observed.time() > clock_time(15, 30):
        raise ValueError('Data collection window expired; cycle aborted')
    engine.event('input', {'prices': prices, 'histories': {c: [asdict(b) for b in rows] for c, rows in histories.items()}})
    events = engine.cycle(observed, prices, histories, kill=kill_file.exists(),
                          allow_entries=clock_time(9, 10) <= observed.time() <= clock_time(15))
    return {'status': 'ok', 'at': observed.isoformat(), 'events': events}


def replay(data: dict[str, list[Bar]], cfg: BotConfig, db: Path, start: str, end: str) -> dict:
    """Daily sampled replay: prior close signal, next session open execution."""
    if db.exists():
        raise ValueError('Backtest DB already exists; select a new path')
    codes = set(cfg.universe) | {cfg.benchmark}
    if not codes.issubset(data):
        raise ValueError('Dataset is missing configured symbols')
    for code in codes:
        rows = data[code]
        if any(a.day >= b.day for a, b in zip(rows, rows[1:])):
            raise ValueError('Dataset must be unique and chronological')
    maps = {c: {b.day: b for b in data[c]} for c in codes}
    dates = sorted({b.day for c in codes for b in data[c] if start <= b.day <= end})
    if not dates or any(day not in maps[c] for day in dates for c in codes):
        raise ValueError('Missing sessions: use a complete common dataset')
    if any(sum(b.day < dates[0] for b in data[c]) < 61 for c in codes):
        raise ValueError('Dataset needs 61 warm-up bars before start')
    engine = PaperEngine(db, cfg)
    for day in dates:
        history = {c: [b for b in data[c] if b.day < day] for c in codes}
        at = datetime.combine(date.fromisoformat(day), clock_time(9), KST)
        engine.cycle(at, {c: maps[c][day].open for c in codes}, history)
        # Close marking uses only known prior-day signals; no close-price entries.
        engine.cycle(at.replace(hour=15, minute=30), {c: maps[c][day].close for c in codes},
                     history, allow_entries=False)
    report = engine.status()
    report['backtest'] = dict(start=start, end=end, sessions=len(dates),
                              execution='next_open_and_close_sampled_stops',
                              limitation='No intraday path, spreads, halts, dividends or survivorship correction')
    benchmark = [maps[cfg.benchmark][d] for d in dates]
    report['benchmark_buy_hold_pct_before_costs'] = (benchmark[-1].close / benchmark[0].open - 1) * 100
    return report


def main():
    parser = argparse.ArgumentParser(description='KIS local paper trading and chronological strategy validation')
    parser.add_argument('--config', type=Path, default=Path('bot.example.json'))
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--db', type=Path, default=Path('.cache/paper.sqlite3'))
    run.add_argument('--cycles', type=int, default=1, help='0 runs until Ctrl+C')
    run.add_argument('--interval', type=float, default=60)
    run.add_argument('--kill-file', type=Path, default=Path('.cache/STOP'))
    status = sub.add_parser('status')
    status.add_argument('--db', type=Path, default=Path('.cache/paper.sqlite3'))
    halt = sub.add_parser('halt')
    halt.add_argument('--db', type=Path, default=Path('.cache/paper.sqlite3'))
    data = sub.add_parser('download')
    data.add_argument('--start', required=True)
    data.add_argument('--end', required=True)
    data.add_argument('--output', type=Path, required=True)
    backtest = sub.add_parser('backtest')
    backtest.add_argument('--data', type=Path, required=True)
    backtest.add_argument('--start', required=True)
    backtest.add_argument('--end', required=True)
    backtest.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        cfg = BotConfig.load(args.config)
        if args.command in ('download', 'backtest'):
            if date.fromisoformat(args.start) > date.fromisoformat(args.end):
                raise ValueError('Start must precede end')
        if args.command == 'download':
            rows = collect(KisClient(Settings.from_env()), cfg, args.start, args.end)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8') as stream:
                json.dump({c: [asdict(b) for b in bs] for c, bs in rows.items()}, stream)
            print(json.dumps({'saved': str(args.output), 'bars': {c: len(bs) for c, bs in rows.items()}}))
            return
        if args.command == 'backtest':
            content = args.data.read_bytes()
            raw = json.loads(content)
            report = replay({c: [Bar(**b) for b in bs] for c, bs in raw.items()},
                            cfg, args.db, args.start, args.end)
            report['dataset_sha256'] = hashlib.sha256(content).hexdigest()
            PaperEngine(args.db, cfg).event('dataset', {'sha256': report['dataset_sha256']})
            print(json.dumps(report, indent=2))
            return
        if args.command in ('status', 'halt') and not args.db.exists():
            raise ValueError('No paper account yet; run the bot first')
        engine = PaperEngine(args.db, cfg)
        if args.command == 'halt':
            engine.halt()
            print('Halted persistently; positions exit on next successful in-session cycle.')
            return
        if args.command == 'status':
            print(json.dumps(engine.status(), indent=2))
            return
        if args.cycles < 0 or not 30 <= args.interval <= 86400:
            raise ValueError('cycles must be >= 0; interval must be 30..86400 seconds')
        client = KisClient(Settings.from_env())
        count, failures = 0, 0
        while args.cycles == 0 or count < args.cycles:
            try:
                print(json.dumps(run_once(client, engine, args.kill_file)), flush=True)
                failures = 0
            except (KisApiError, requests.RequestException, ValueError) as exc:
                failures += 1
                # Persist error type only; upstream response text can contain credentials.
                engine.event('error', {'type': type(exc).__name__, 'consecutive': failures})
                LOG.error('Cycle failed (%s); no simulated fills committed', type(exc).__name__)
                if failures >= 3 or args.cycles == 1:
                    raise ValueError('Feed failed; inspect bot_events and retry after recovery') from None
            count += 1
            if args.cycles == 0 or count < args.cycles:
                time.sleep(min(args.interval * 2 ** failures, 86400))
        if failures:
            raise ValueError('Final cycle failed')
    except KeyboardInterrupt:
        print('Stopped; paper positions retained in DB.')
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, KisApiError, requests.RequestException) as exc:
        LOG.error('Bot stopped (%s)', type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
