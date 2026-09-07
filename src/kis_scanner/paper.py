from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .strategy import Bar, decide

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class BotConfig:
    initial_cash: float = 10_000_000
    risk_per_trade: float = 0.005
    max_position: float = 0.15
    max_exposure: float = 0.60
    max_positions: int = 4
    daily_loss: float = 0.02
    max_drawdown: float = 0.10
    stop_atr: float = 2.0
    fee_bps: float = 5.0
    sell_tax_bps: float = 20.0
    slippage_bps: float = 10.0
    benchmark: str = '069500'
    universe: tuple[str, ...] = ('005930', '000660', '035420', '005380', '051910')

    def __post_init__(self):
        numeric = [v for v in asdict(self).values() if isinstance(v, (float, int))]
        if not all(math.isfinite(v) for v in numeric):
            raise ValueError('Config numbers must be finite')
        if self.initial_cash <= 0 or self.stop_atr <= 0:
            raise ValueError('Capital and ATR multiplier must be positive')
        for name in ('risk_per_trade', 'max_position', 'max_exposure', 'daily_loss', 'max_drawdown'):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f'Invalid fraction: {name}')
        if not isinstance(self.max_positions, int) or not 1 <= self.max_positions <= 100:
            raise ValueError('Invalid max_positions')
        if not all(0 <= x < 1000 for x in (self.fee_bps, self.sell_tax_bps, self.slippage_bps)):
            raise ValueError('Invalid transaction costs')
        if not self.universe or len(set(self.universe)) != len(self.universe):
            raise ValueError('Universe must be nonempty and unique')
        if any(len(c) != 6 or not c.isdigit() for c in (*self.universe, self.benchmark)):
            raise ValueError('Expected six-digit KRX codes')

    @classmethod
    def load(cls, path: Path) -> BotConfig:
        raw = json.loads(path.read_text(encoding='utf-8'))
        if 'universe' in raw:
            raw['universe'] = tuple(raw['universe'])
        return cls(**raw)


class PaperEngine:
    """Local simulation only. State, fills and equity commit atomically."""

    def __init__(self, path: Path, config: BotConfig):
        self.path, self.config = path, config
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS bot_state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bot_events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bot_equity (at TEXT PRIMARY KEY, equity REAL NOT NULL, cash REAL NOT NULL);
            ''')
            state = dict(config=asdict(config), cash=config.initial_cash, positions={},
                         peak=config.initial_cash, day='', day_start=config.initial_cash,
                         halted=False, daily_halted=False, entered=[], last_at='')
            db.execute('INSERT OR IGNORE INTO bot_state VALUES(1, ?)', (json.dumps(state),))
            stored = json.loads(db.execute('SELECT body FROM bot_state').fetchone()[0])
            if stored['config'] != json.loads(json.dumps(asdict(config))):
                raise ValueError('Account config differs; use a new DB for a different experiment')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def event(self, kind: str, details: dict):
        with self.connect() as db:
            db.execute('INSERT INTO bot_events(at,kind,body) VALUES(?,?,?)',
                       (datetime.now(KST).isoformat(), kind, json.dumps(details)))

    def status(self) -> dict:
        with self.connect() as db:
            state = json.loads(db.execute('SELECT body FROM bot_state').fetchone()[0])
            curve = db.execute('SELECT at,equity,cash FROM bot_equity ORDER BY at').fetchall()
            sells = [json.loads(r[0]) for r in db.execute("SELECT body FROM bot_events WHERE kind='sell'")]
            fills = db.execute("SELECT COUNT(*) FROM bot_events WHERE kind IN ('buy','sell')").fetchone()[0]
            recent = db.execute("SELECT at,kind,body FROM bot_events WHERE kind != 'input' ORDER BY id DESC LIMIT 5").fetchall()
        peak, drawdown = self.config.initial_cash, 0.0
        for _, equity, _ in curve:
            peak = max(peak, equity)
            drawdown = max(drawdown, 1 - equity / peak)
        return dict(mode='local-paper', state=state, latest_equity=curve[-1] if curve else None,
                    fills=fills, closed_trades=len(sells), recent_events=recent,
                    realized_pnl=sum(s['pnl'] for s in sells),
                    win_rate=sum(s['pnl'] > 0 for s in sells) / len(sells) if sells else None,
                    return_pct=((curve[-1][1] / self.config.initial_cash - 1) * 100) if curve else 0,
                    max_drawdown_pct=drawdown * 100)

    def halt(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT body FROM bot_state').fetchone()[0])
            state['halted'] = True
            db.execute('UPDATE bot_state SET body=? WHERE id=1', (json.dumps(state),))
            db.execute("INSERT INTO bot_events(at,kind,body) VALUES(?, 'halt_requested', '{}')",
                       (datetime.now(KST).isoformat(),))

    def cycle(self, at: datetime, prices: dict[str, float], histories: dict[str, list[Bar]],
              *, kill: bool = False, allow_entries: bool = True) -> list[dict]:
        if at.tzinfo is None:
            raise ValueError('Timezone-aware timestamp required')
        at = at.astimezone(KST)
        stamp, day = at.isoformat(), at.date().isoformat()
        cfg, events = self.config, []
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT body FROM bot_state').fetchone()[0])
            if state['last_at'] and at <= datetime.fromisoformat(state['last_at']):
                return []
            positions = state['positions']
            required = set(cfg.universe) | set(positions) | {cfg.benchmark}
            if any(c not in prices or not math.isfinite(prices[c]) or prices[c] <= 0 for c in required):
                raise ValueError('Incomplete or invalid prices; cycle aborted')
            if any(c not in histories or not histories[c] for c in required):
                raise ValueError('Incomplete histories; cycle aborted')
            if any(b.day >= day for c in required for b in histories[c]):
                raise ValueError('Execution-day/future bars are forbidden')
            decisions = {c: decide(histories[c]) for c in required}
            equity = state['cash'] + sum(p['qty'] * prices[c] for c, p in positions.items())
            if state['day'] != day:
                # Previous marked equity includes overnight gaps in the daily loss limit.
                previous = db.execute('SELECT equity FROM bot_equity ORDER BY at DESC LIMIT 1').fetchone()
                state.update(day=day, day_start=previous[0] if previous else cfg.initial_cash,
                             entered=[], daily_halted=False)
            state['peak'] = max(state['peak'], equity)
            state['halted'] |= kill or equity <= state['peak'] * (1 - cfg.max_drawdown)
            state['daily_halted'] |= equity <= state['day_start'] * (1 - cfg.daily_loss)
            halted = state['halted'] or state['daily_halted']
            for code, p in list(positions.items()):
                px = prices[code]
                reason = ('risk_halt' if halted else 'stop' if px <= p['stop'] else
                          'trend_exit' if decisions[code].exit else '')
                if reason:
                    fill = px * (1 - cfg.slippage_bps / 10_000)
                    proceeds = p['qty'] * fill * (1 - (cfg.fee_bps + cfg.sell_tax_bps) / 10_000)
                    state['cash'] += proceeds
                    events.append(dict(kind='sell', code=code, qty=p['qty'], price=fill,
                                       pnl=proceeds - p['cost'], reason=reason))
                    del positions[code]
                    state['entered'].append(code)
                elif decisions[code].atr > 0:
                    p['stop'] = max(p['stop'], px - cfg.stop_atr * decisions[code].atr)
            market = histories[cfg.benchmark]
            regime = len(market) >= 60 and market[-1].close > sum(b.close for b in market[-60:]) / 60
            # Include exit costs before deciding whether more risk may be added.
            equity = state['cash'] + sum(p['qty'] * prices[c] for c, p in positions.items())
            state['daily_halted'] |= equity <= state['day_start'] * (1 - cfg.daily_loss)
            state['halted'] |= equity <= state['peak'] * (1 - cfg.max_drawdown)
            halted = state['halted'] or state['daily_halted']
            for code in cfg.universe:
                d, px = decisions[code], prices[code]
                if not allow_entries or halted or not regime or not d.enter or code in positions or code in state['entered']:
                    continue
                if len(positions) >= cfg.max_positions:
                    break
                if abs(px / histories[code][-1].close - 1) > 0.03:
                    continue
                exposure = sum(p['qty'] * prices[c] for c, p in positions.items())
                equity = state['cash'] + exposure
                fill = px * (1 + cfg.slippage_bps / 10_000)
                unit_cost = fill * (1 + cfg.fee_bps / 10_000)
                distance = cfg.stop_atr * d.atr
                if distance <= 0 or distance >= px:
                    continue
                risk_cost = distance + px * (2 * cfg.slippage_bps + 2 * cfg.fee_bps + cfg.sell_tax_bps) / 10_000
                qty = int(min(equity * cfg.risk_per_trade / risk_cost,
                              equity * cfg.max_position / unit_cost,
                              max(0, equity * cfg.max_exposure - exposure) / unit_cost,
                              state['cash'] / unit_cost))
                if qty <= 0:
                    continue
                cost = qty * unit_cost
                state['cash'] -= cost
                positions[code] = dict(qty=qty, cost=cost, entry=fill, stop=px - distance)
                state['entered'].append(code)
                events.append(dict(kind='buy', code=code, qty=qty, price=fill, reason='trend_breakout'))
            equity = state['cash'] + sum(p['qty'] * prices[c] for c, p in positions.items())
            state['last_at'] = stamp
            for event in events:
                db.execute('INSERT INTO bot_events(at,kind,body) VALUES(?,?,?)',
                           (stamp, event['kind'], json.dumps(event)))
            db.execute("INSERT INTO bot_events(at,kind,body) VALUES(?, 'cycle', ?)",
                       (stamp, json.dumps(dict(halted=halted, regime=regime, equity=equity))))
            db.execute('INSERT INTO bot_equity VALUES(?,?,?)', (stamp, equity, state['cash']))
            db.execute('UPDATE bot_state SET body=? WHERE id=1', (json.dumps(state),))
        return events
