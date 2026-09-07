from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from kis_scanner.bot import replay, run_once
from kis_scanner.paper import BotConfig, KST, PaperEngine
from kis_scanner.strategy import Bar, decide
from kis_scanner.models import PriceSnapshot
from kis_scanner import bot


def history():
    rows = []
    for i in range(61):
        close = 10000 + i * 10
        if i == 60:
            close += 200
        rows.append(Bar((date(2025, 1, 1) + timedelta(days=i)).isoformat(),
                        close - 10, close + 50, close - 50, close,
                        2_000_000 if i == 60 else 1_000_000))
    return rows


@pytest.fixture
def setup(tmp_path):
    cfg = BotConfig(universe=('005930',), benchmark='069500')
    engine = PaperEngine(tmp_path / 'paper.db', cfg)
    rows = history()
    at = datetime(2025, 3, 3, 9, 10, tzinfo=KST)
    return engine, at, {'005930': 10800, '069500': 10800}, {'005930': rows, '069500': rows}


def test_breakout_requires_trend_volume_and_liquidity():
    rows = history()
    assert decide(rows).enter
    assert not decide(rows[:60]).enter
    assert not decide([replace(b, volume=1) for b in rows]).enter
    rows[-1] = replace(rows[-1], volume=1_000_000)
    assert not decide(rows).enter


def test_position_sizing_costs_restart_and_idempotency(setup):
    engine, at, prices, histories = setup
    events = engine.cycle(at, prices, histories)
    assert len(events) == 1 and events[0]['kind'] == 'buy'
    state = engine.status()['state']
    position = state['positions']['005930']
    assert position['cost'] <= engine.config.initial_cash * engine.config.max_position
    assert state['cash'] >= 0
    assert position['entry'] > prices['005930']
    assert engine.cycle(at, prices, histories) == []
    restarted = PaperEngine(engine.path, engine.config)
    assert restarted.cycle(at + timedelta(minutes=5), prices, histories) == []
    assert restarted.status()['fills'] == 1


def test_gap_stop_fills_at_observed_price_and_no_reentry(setup):
    engine, at, prices, histories = setup
    engine.cycle(at, prices, histories)
    prices['005930'] = 10000
    sells = engine.cycle(at + timedelta(minutes=5), prices, histories)
    assert len(sells) == 1 and sells[0]['kind'] == 'sell'
    assert sells[0]['price'] < 10000
    assert sells[0]['pnl'] < 0
    prices['005930'] = 10800
    assert engine.cycle(at + timedelta(minutes=10), prices, histories) == []


def test_missing_feed_rolls_back_all_changes(setup):
    engine, at, prices, histories = setup
    before = engine.status()
    del prices['069500']
    with pytest.raises(ValueError, match='Incomplete'):
        engine.cycle(at, prices, histories)
    assert before == engine.status()


def test_future_bars_and_nonfinite_prices_rejected(setup):
    engine, at, prices, histories = setup
    histories['005930'] = histories['005930'] + [replace(histories['005930'][-1], day=at.date().isoformat())]
    with pytest.raises(ValueError, match='future'):
        engine.cycle(at, prices, histories)
    prices['005930'] = float('nan')
    with pytest.raises(ValueError, match='invalid prices'):
        engine.cycle(at, prices, histories)


def test_kill_is_persistent_and_liquidates(setup):
    engine, at, prices, histories = setup
    engine.cycle(at, prices, histories)
    events = engine.cycle(at + timedelta(minutes=5), prices, histories, kill=True)
    assert events[0]['reason'] == 'risk_halt'
    assert engine.status()['state']['halted']
    assert engine.cycle(at + timedelta(days=1), prices, histories) == []


def test_daily_loss_latches_even_after_price_recovers(setup):
    engine, at, prices, histories = setup
    engine.cycle(at, prices, histories)
    prices['005930'] = 5000
    engine.cycle(at + timedelta(minutes=5), prices, histories)
    assert engine.status()['state']['daily_halted']
    prices['005930'] = 10800
    assert engine.cycle(at + timedelta(minutes=10), prices, histories) == []


def test_config_change_cannot_silently_rewrite_account(setup):
    engine, *_ = setup
    with pytest.raises(ValueError, match='config differs'):
        PaperEngine(engine.path, replace(engine.config, fee_bps=1))


def test_bear_market_blocks_new_entries(setup):
    engine, at, prices, histories = setup
    histories['069500'] = [replace(b, open=9000, high=9050, low=8900, close=8950)
                          if i == 60 else b for i, b in enumerate(histories['069500'])]
    assert engine.cycle(at, prices, histories) == []


def test_backtest_next_open_and_existing_db_rejected(tmp_path):
    cfg = BotConfig(universe=('005930',))
    rows = history() + [Bar('2025-03-03', 10900, 11000, 10700, 10950, 1000000)]
    data = {c: rows for c in (*cfg.universe, cfg.benchmark)}
    path = tmp_path / 'test.db'
    result = replay(data, cfg, path, '2025-03-03', '2025-03-03')
    assert result['state']['positions']['005930']['entry'] == pytest.approx(10900 * 1.001)
    with pytest.raises(ValueError, match='already exists'):
        replay(data, cfg, path, '2025-03-03', '2025-03-03')


@pytest.mark.parametrize('kwargs', [{'risk_per_trade': 0}, {'initial_cash': -1},
                                    {'slippage_bps': float('nan')}, {'max_positions': 1.5}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        BotConfig(**kwargs)


def test_halt_command_survives_restart(setup):
    engine, at, prices, histories = setup
    engine.cycle(at, prices, histories)
    engine.halt()
    restored = PaperEngine(engine.path, engine.config)
    assert restored.status()['state']['halted']
    assert restored.cycle(at + timedelta(minutes=5), prices, histories)[0]['kind'] == 'sell'


def test_transaction_rolls_back_on_database_failure(setup):
    engine, at, prices, histories = setup
    before = engine.status()
    with engine.connect() as db:
        db.execute("CREATE TRIGGER fail_fill BEFORE INSERT ON bot_events BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        engine.cycle(at, prices, histories)
    assert engine.status() == before


def test_runner_rejects_holiday_and_stale_quotes(setup, monkeypatch, tmp_path):
    engine, at, prices, histories = setup

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return at

    class Feed:
        def get_current_price(self, code):
            return PriceSnapshot(code, 10800, 0, 0, 0)

    monkeypatch.setattr(bot, 'datetime', FixedDatetime)
    monkeypatch.setattr(bot.time, 'sleep', lambda _: None)
    monkeypatch.setattr(bot, 'collect', lambda *_: histories)
    result = run_once(Feed(), engine, tmp_path / 'STOP')
    assert result['reason'] == 'missing_current_session_bar'
    today = Bar(at.date().isoformat(), 10800, 10900, 10700, 10800, 100)
    monkeypatch.setattr(bot, 'collect', lambda *_: {c: rows + [today] for c, rows in histories.items()})
    with pytest.raises(ValueError, match='volume predates'):
        run_once(Feed(), engine, tmp_path / 'STOP')
    assert engine.status()['fills'] == 0


def test_backtest_missing_symbol_session_rejected(tmp_path):
    cfg = BotConfig(universe=('005930',))
    rows = history() + [Bar('2025-03-03', 10900, 11000, 10700, 10950, 1000000)]
    with pytest.raises(ValueError, match='Missing sessions'):
        replay({'005930': rows, '069500': history()}, cfg, tmp_path / 'x.db', '2025-03-03', '2025-03-03')
