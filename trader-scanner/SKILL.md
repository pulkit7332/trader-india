---
name: trader-scanner
description: >-
  Chart Scanner (India). Scans NSE/BSE cash-equity charts for setups:
  opening-range breakouts, flags, oversold bounces. Builds the universe from
  NSE's own EQUITY_L.csv symbol list (cash-market "EQ" series only; BE/BZ
  trade-for-trade symbols excluded since they cannot be day-traded), runs a
  pre-market scan of daily closes (20-day closing-high breakout, bull flag,
  reclaimed the 20-day average, oversold, oversold bounce) and an intraday
  opening-range breakout scan on 5-minute bars, then writes
  trader_in/watchlist.json for trader-news and trader-entry. Free Yahoo data
  (.NS / .BO tickers), bar by bar, computed in IST. Use when the user says
  "scan the market", "find me setups", "what is breaking out", "opening
  range breakouts", "scan for bull flags", "oversold stocks", "pre-market
  scan", "build my watchlist", "what should I look at today", or at the
  start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner (India)

The Chart Scanner is the first step of a day trade on NSE or BSE. It reads
free charts and lists the ones that match a rule. That is all it does. A
scan is a list of charts that match a rule, not a list of stocks to buy, and
a setup on a chart is not a prediction. Most people who day trade lose
money; the scanner does not change that.

This is the India-market sibling of the original Alpaca-based `day-trader-skill`
pack (US equities, NYSE hours, Alpaca order format). It shares the same
file-based design but reads NSE/BSE data, works in IST and writes to its own
`trader_in/` folder so the two packs never collide if both are installed.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # -> trader_in/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before 9:15 IST -> trader_in/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,INFY
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:35 IST -> trader_in/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader_in/watchlist.json --json
```

Flags: `--min-price 20` and `--min-volume 100000` (shares, not rupees),
`--or-minutes 15`, `--max-confirm 200` (orb stage 2), `--now "2026-10-29
10:15"` (IST) to pin the clock, `--offline PATH` to read a saved NSE
EQUITY_L.csv, `--no-write`, `--show N`, `--json`. Exit code 0 when the scan
found something, 1 when it found nothing or could not run.

## What it reads and writes

| file | what |
| --- | --- |
| `trader_in/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line (bare NSE symbol, no `.NS`), read by `daily` and `orb` |
| `trader_in/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |

Symbols in every file are bare NSE tickers (`RELIANCE`, not `RELIANCE.NS`);
every script in this pack appends `.NS` for a Yahoo lookup unless you give it
a ticker that already ends `.NS` or `.BO`.

## The universe

`universe` downloads `EQUITY_L.csv` from `archives.nseindia.com` (the
archive host; NSE's live `nseindia.com` API is far more likely to rate-limit
or block scripted access) and keeps only rows with `SERIES == EQ`. NSE's `BE`
and `BZ` series are **trade-for-trade**: no intraday netting is allowed, so a
buy must settle by delivery and cannot be squared off the same day. Those
symbols are excluded because they cannot be day-traded at all, not because
they are illiquid. On 2026-10-29 this keeps 2,357 of 2,620 listed symbols.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for up to 20 symbols a request. Run it
before 9:15 IST or after 15:30: during the session the day's own bar is
still forming. Liquidity comes from the spark meta: last price at least
`--min-price` (₹20 default — NSE has far more sub-₹100 liquid names than the
US market, so the floor is much lower than the Alpaca pack's $5) and last
session's volume at least `--min-volume` (100,000 shares default — share
counts, not dollar volume, since NSE lot-free cash equities trade in single
shares at very different price points).

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10% over the 10 sessions before the last 5, then the last 5 closes inside a 5% range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

## The opening-range breakout scan (during the session)

NSE's cash session is **09:15–15:30 IST** (pre-open 09:00–09:08; this tool
does not trade the pre-open auction). The opening range is the first
`--or-minutes` (15) of the session: the 9:15, 9:20 and 9:25 five-minute
bars. A hit is the first completed 5-minute bar to CLOSE above the range
high, at that bar's close time, IST. There is no daylight saving to account
for (India keeps one fixed offset, UTC+5:30, all year), so unlike the NYSE
version this clock math never changes across the year.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed
   at or above the range's highest close.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each, most traded first, capped at `--max-confirm` (200;
   `--max-confirm 0` checks them all). Prints PARTIAL with the count skipped.

Only bars that have fully closed by the pinned clock (`--now`, or the real
time) are ever used — a live, in-progress bar is never counted, and a `--now`
in the past (for review or testing) clips out any bar that has not happened
yet by that time.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. This is
  the **solid** data source in this pack: `.NS` and `.BO` tickers return real
  OHLCV in INR, same shape as the US pack's data. query2 first, query1 on a
  429 or 5xx, one retry after a wait on a 429.
- NSE's `EQUITY_L.csv` archive is generally reliable; the **flaky** piece of
  this pack's data is NSE's live `nseindia.com` API (used by trader-news),
  which is known to rate-limit or outright block requests from outside India
  or from data-center IPs. The static `archives.nseindia.com` CSV used here
  has not shown that problem in testing.
- NSE/BSE holidays for 2026 only (see trader-risk's SKILL.md for the list and
  its caveats on movable festival dates). Outside 2026 the calendar is
  unknown, and even for 2026 the movable Hindu/Islamic holidays should be
  checked against NSE's own circular before relying on this list for real
  trading decisions.

## Steps

1. Once a week or so: `scan.py universe`.
2. Before 9:15 IST: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 IST (the classic cutoff for a 15-minute range): `scan.py orb`.
   Tell the user how many usable charts broke their range and when.
4. The user picks one chart. Hand it on: `/trader-news TICKER` (what is
   moving it), then `/trader-entry TICKER` (the entry bar by bar). Never pick
   for them.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data (IST).
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Differences from the Alpaca (US) pack

- NYSE 9:30–16:00 ET → NSE 9:15–15:30 IST, no daylight saving, no 1:00 p.m.
  early closes (NSE's one calendar quirk, Muhurat trading, is a special
  one-hour evening session on an otherwise-closed Diwali holiday, not an
  early close — this pack does not scan that session).
- Universe comes from NSE's own `EQUITY_L.csv`, filtered to the `EQ` series,
  instead of Nasdaq Trader's symbol directories; the India-specific exclusion
  is `BE`/`BZ` trade-for-trade stocks, not ETFs/warrants/units.
- Prices are in INR with a much lower liquidity floor (₹20 vs $5) since a
  large share of liquid NSE names trade well under ₹100.

## Safety

- Education and tooling, not financial advice. A hit is a rules match, not a
  recommendation.
- Paper/manual only. **This pack has no live broker integration at all** —
  not Kite Connect, not any other API. trader-broker in this pack only
  prints an order ticket for you to enter yourself at your own broker.
- Nothing in this pack ever places a real order anywhere.
