from datetime import datetime

import pytest

from kis_scanner import bot
from kis_scanner.paper import BotConfig, PaperEngine, daily_equity


def test_first_day_compares_initial_capital_not_first_intraday_mark():
    rows = daily_equity([
        ('2026-09-04T09:10:00+09:00', 990, 500),
        ('2026-09-04T15:20:00+09:00', 1010, 510),
    ], 1000)
    assert len(rows) == 1
    assert rows[0]['baseline_kind'] == 'initial_capital'
    assert rows[0]['baseline_at'] is None
    assert rows[0]['change_krw'] == 10
    assert rows[0]['change_pct'] == pytest.approx(1)
    assert rows[0]['holdings_value'] == 500


def test_weekend_uses_last_recorded_day_not_same_day_open():
    rows = daily_equity([
        ('2026-09-04T15:20:00+09:00', 1000, 400),
        ('2026-09-07T09:10:00+09:00', 1050, 400),
        ('2026-09-07T14:20:00+09:00', 980, 400),
    ], 1000)
    assert len(rows) == 2
    last = rows[-1]
    assert last['baseline_at'] == '2026-09-04T15:20:00+09:00'
    assert last['baseline_equity'] == 1000
    assert last['change_krw'] == -20
    assert last['change_pct'] == pytest.approx(-2)


def test_kst_date_and_chronological_order():
    rows = daily_equity([
        ('2026-09-07T01:00:00+00:00', 1100, 1100),
        ('2026-09-06T23:00:00+00:00', 1050, 1050),
        ('2026-09-04T06:00:00+00:00', 1000, 1000),
    ], 1000)
    assert len(rows) == 2
    assert rows[-1]['date'] == '2026-09-07'
    assert rows[-1]['observed_at'] == '2026-09-07T10:00:00+09:00'
    assert rows[-1]['change_krw'] == 100


def test_no_data_and_zero_baseline():
    assert daily_equity([], 1000) == []
    rows = daily_equity([
        ('2026-09-04T15:00:00+09:00', 0, 0),
        ('2026-09-07T15:00:00+09:00', 0, 0),
    ], 1000)
    assert rows[-1]['change_pct'] is None


def test_history_limit_preserves_previous_day_baseline_and_restart(tmp_path):
    path = tmp_path / 'paper.db'
    engine = PaperEngine(path, BotConfig())
    assert engine.status()['daily_change'] is None
    with engine.connect() as db:
        db.executemany('INSERT INTO bot_equity VALUES(?,?,?)', [
            ('2026-09-04T15:00:00+09:00', 10_000_000, 5_000_000),
            ('2026-09-07T15:00:00+09:00', 10_200_000, 5_000_000),
        ])
    restored = PaperEngine(path, BotConfig())
    rows = restored.equity_history(1)
    assert len(rows) == 1
    assert rows[0]['change_krw'] == 200_000
    assert restored.status()['daily_change'] == rows[0]
    with pytest.raises(ValueError):
        restored.equity_history(0)


def test_equity_cli_reads_without_api_and_displays_asof(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'paper.db'
    engine = PaperEngine(path, BotConfig())
    with engine.connect() as db:
        db.execute('INSERT INTO bot_equity VALUES(?,?,?)',
                   ('2026-09-04T15:00:00+09:00', 10_000_000, 10_000_000))
    monkeypatch.setattr(bot.BotConfig, 'load', lambda _: BotConfig())
    monkeypatch.setattr(bot.Settings, 'from_env', lambda: pytest.fail('No API credentials needed'))
    monkeypatch.setattr('sys.argv', ['kis-bot', 'equity', '--db', str(path)])
    bot.main()
    output = capsys.readouterr().out
    assert 'initial capital' in output
    assert '10,000,000' in output
    assert '2026-09-04T15:00:00+09:00' in output
