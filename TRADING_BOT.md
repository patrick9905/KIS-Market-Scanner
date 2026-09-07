# KIS Trading Bot: Local Paper Trading

This release simulates trading locally using KIS market data. It does not send
orders, access balances, or implement a live brokerage adapter. It is also not
the KIS virtual brokerage account. Existing scan/save/track commands remain available.

## Strategy

The original intraday momentum score remains a research scanner. The bot uses
a fixed, configurable universe to make historical tests reproducible. The
example symbols are a research universe, not recommendations. Universe order
is the deterministic priority when multiple symbols qualify.

Entries use at least 61 completed daily bars, excluding today's bar:

- Close above the previous 20-session high.
- Close > 20-day average > 60-day average, with rising 20-day average.
- Volume at least 1.5 times the preceding 20-day average.
- Average close times volume at least KRW 5 billion over 20 sessions.
- 14-day average true range between 0.5% and 5% of price.
- Benchmark ETF 069500 above its 60-day average.
- Current price within 3% of the signal close.

Exit when the observed price crosses a 2-ATR trailing stop or yesterday's close
falls below its 20-day average. Stops never move down. Position sizing includes
estimated transaction costs in the risk budget. No leverage, shorts or averaging
down are implemented. A sold symbol cannot re-enter that day.

## Defaults

| Parameter | Example assumption |
| --- | --- |
| Paper capital | KRW 10,000,000 |
| Planned risk per entry | 0.5% of equity |
| Per-symbol allocation | Up to 15% at entry |
| Total allocation | Up to 60% at entry, at most 4 positions |
| Daily loss trigger | 2%, including marked overnight gaps |
| Peak-equity drawdown trigger | 10%, latched across restarts |
| Fee | 5 basis points per side |
| Sell tax | 20 basis points |
| Slippage | 10 basis points per side |

Fees/tax are deliberately explicit modeling assumptions, not a statement of
current broker fees or statutory rates. Adjust for the instrument/account before
research. Risk limits are triggers, not guaranteed maximum losses; gaps,
disconnections and polling intervals can lead to larger losses. Allocation
ceilings apply at entry, not as continuous rebalancing constraints.

## Run

From the project directory with the existing .env:

```powershell
py -m pip install -e ".[dev]"
kis-bot run
kis-bot status
kis-bot run --cycles 0 --interval 60
```

`run` alone performs one cycle. The last command runs in the foreground until
Ctrl+C. The computer must remain awake and connected. It is not an installed
Windows service or a scheduled background job. Ctrl+C preserves the simulated
account and holdings; it does not liquidate them. Re-running restores them.

Market monitoring is restricted to weekdays 09:00-15:30 KST; new entries to
09:10-15:00. Same-day daily bars and positive traded volume are required for all
symbols, preventing weekday-holiday fills. This is not an exchange calendar:
special sessions and suspended symbols can result in skipped cycles. Quotes do
not provide an exchange timestamp through the existing price model; a same-day
bar and a collection window of at most 60 seconds are only partial freshness
checks. This is a material limitation for live execution.

Each cycle fetches history and prices with pacing, then waits the interval.
Actual observation spacing therefore exceeds the interval. A missing/invalid
symbol aborts the whole cycle. API failures back off; three consecutive failures
terminate the process. A failed finite run exits nonzero. No order is retried,
because there are no broker orders in this release.

```powershell
kis-bot halt
kis-bot status
```

`halt` persists an entry block immediately. The running bot simulates liquidation
on its next successful in-session cycle with complete data. It does not fetch
prices or close positions by itself. A `.cache/STOP` file has the same effect
when detected in a successful cycle. Removing it does not clear a latched halt.
There is intentionally no automatic resume after the peak-drawdown/explicit halt;
use a separate research DB after reviewing the results. Daily halts reset on the
next observed session. Existing account configuration cannot be silently changed.

## Research

```powershell
kis-bot download --start 2023-01-01 --end 2026-09-04 --output .cache/prices.json
kis-bot backtest --data .cache/prices.json --start 2024-01-01 --end 2025-12-31 --db .cache/research-a.sqlite3
kis-bot backtest --data .cache/prices.json --start 2026-01-01 --end 2026-09-04 --db .cache/research-b.sqlite3
```

Output files and backtest DBs must be new paths to prevent accidental overwrite.
Include at least 61 sessions before the test start for indicator warm-up. Data
requests page backward in batches of up to 100 bars. The test requires all symbols
on every tested date. The dataset hash is recorded for reproducibility.

Signals are computed from completed prior days, with simulated next-open entries.
Stops are checked at open and close only: intraday high/low order is unknown and
therefore intraday stop behavior is NOT modeled. The same decision/accounting
engine runs online, but its polling frequency differs. Results include configured
costs and open-position mark-to-market; final positions are not forcibly sold.
Reported drawdown uses sampled equity, not intraday worst-case equity.

Data use adjusted prices. Dividends, historical universe membership, corporate
actions affecting share quantities, suspensions, queues, partial fills and market
impact are not modeled. Fixed present-day symbols introduce selection/survivorship
bias. These limitations prevent using this test as evidence for live readiness.

## Initial Results

KIS returned 897 daily bars per symbol for six symbols, 2023-01-01 through
2026-09-04. The defaults were fixed before the first test; no search for more
profitable parameter values was performed. Both periods were inspected during
development, so neither should be represented as an untouched future holdout.

| Period | Sessions | Net return | Sampled max drawdown | Closed trades | Win rate | ETF buy/hold, before costs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2024-2025 | 486 | 7.49% | 2.62% | 24 | 50% | 76.92% |
| 2026 through Sep 4 | 166 | 1.03% | 2.12% | 5 | 40% | 74.86% |

The strategy substantially underperformed the fully invested benchmark. Exposure
differs, but these results do not establish a useful trading edge or stable income.
Five closed trades in the second period are especially insufficient evidence.
Preserve this baseline and collect forward paper results before revising a strategy.

Live trading still requires a broker adapter with balance/order reconciliation,
idempotent order submission, partial-fill handling, exchange calendars and fresh
quotes, corporate-action handling, monitoring/alerts and recovery testing. It also
requires an explicit capital/risk mandate. No live orders were enabled or sent.

## Storage and Verification

`.cache/paper.sqlite3` stores `bot_state`, `bot_events`, `bot_equity`. Account state,
fills and equity are committed in one SQLite transaction. An immediate write lock,
monotonic cycle timestamp, held-position check and same-day entry list protect
against repeated fills. Input snapshots and failures are logged separately.
All timestamps carry the KST offset. `.cache/` and `.env` are ignored by Git.

```powershell
py -m pytest -q
```

References: [KIS official API example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py),
[FINRA automated investment tools](https://www.finra.org/investors/alerts/automated-investment-tools).
