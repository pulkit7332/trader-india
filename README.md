# trader-india

A Claude Code skill pack for semi-automated NSE/BSE day trading research:
scan for setups, time an entry, plan the exit, replay the odds, size the
position, check it against your own risk rules, get a plain-language order
ticket to enter yourself, and watch the alerts — all from free data, all
file-based, all paper/manual.

This is the India-market sibling of the `day-trader-skill` plugin (US
equities via Alpaca, NYSE hours, Alpaca's order format). It mirrors that
pack's structure and safety design closely, but nothing here talks to a
live broker. See **"How this differs from the Alpaca (US) pack"** below for
the full list of adaptations, and each skill's own `SKILL.md` for the
specifics relevant to that stage.

## The pipeline

```
trader-scanner  ->  trader-news  ->  trader-entry  ->  trader-exit  ->  trader-timemachine
                                                                              |
                                                                              v
                                                      trader-alerts  <-  trader-budget
                                                              ^              |
                                                              |              v
                                                        trader-broker  <-  trader-risk
```

1. **trader-scanner** — builds the NSE cash-equity universe from NSE's own
   `EQUITY_L.csv` and scans for setups (20-day breakout, bull flag, reclaim
   of the 20-day average, oversold, oversold bounce, and an intraday
   opening-range breakout) using free Yahoo chart/spark data.
2. **trader-news** — NSE corporate announcements (official filings) plus a
   thin Yahoo-news fallback, per watchlist ticker, tagged and deduplicated.
3. **trader-entry** — watches a setup bar by bar on free 5-minute (or
   1-minute) bars and calls the entry on the first closed bar above the
   opening-range high or a level, computed in IST.
4. **trader-exit** — sets the stop (midpoint/low/percent/ATR) and the target
   (R-multiple) before you enter, rounded to NSE's ₹0.05 tick size.
5. **trader-timemachine** — 10,000 Monte Carlo replays of the rest of the
   session from the stock's own last ~60 days of 5-minute bars, plus the
   real historical record of the same rule.
6. **trader-budget** — sizes the position in plain cash (CNC/delivery)
   terms against your budget, max loss per trade, position cap and cash
   buffer. No leverage, no F&O.
7. **trader-risk** — the hard gate. Blocks any entry that breaks your rules
   (stop required, max loss, reward:risk, daily loss cap, trades/day cap,
   entry window, blocked list, duplicates, daily order cap, NSE calendar, a
   best-effort circuit-limit check). Never blocks an exit.
8. **trader-broker** — turns a risk-passed, user-approved order into a
   plain-language ticket and the exact fields to type into your own broker's
   app. **This pack has no live broker integration of any kind.**
9. **trader-alerts** — price/stop/target/flat-by-close/opening-bell alerts
   from the same free Yahoo data, computed in IST.
10. There is **no Pelosi-tracker equivalent** (no comparable Indian
    disclosure regime for legislators' trades) and **no earnings-analyst
    equivalent** (no Indian EDGAR-style structured, machine-readable filing
    API — see trader-news's `SKILL.md` for why a weaker version was not
    built instead). Both are simply left out rather than faked.

Each stage is a `SKILL.md` plus a single, self-contained, stdlib-only Python
script in its own folder, exactly like the reference pack. Data flows
through plain JSON files in a `trader_in/` folder created in whatever
directory you run these from — a different name from the original pack's
`trader/`, chosen specifically so the two packs never collide if both are
installed and used from the same working directory.

## Install

This repo is an installable Claude Code plugin (`.claude-plugin/plugin.json`
+ `marketplace.json`), the same way the reference `day-trader-skill` pack
ships. Two ways to use it:

**As a plugin, from this repo directly** (recommended if you want updates
via `git pull` to just work):
```
/plugin marketplace add pulkit7332/trader-india
/plugin install trader-india@trader-india
```

**As plain user-level skills**, no plugin machinery at all — clone the repo
and symlink each `trader-*` folder into `~/.claude/skills/`:
```bash
git clone https://github.com/pulkit7332/trader-india.git
for d in trader-india/trader-*; do ln -s "$(pwd)/$d" ~/.claude/skills/"$(basename "$d")"; done
```
Either way, each stage becomes invocable as `/trader-scanner`,
`/trader-entry`, etc.

## Setup

1. Install the pack (see above).
2. Copy `trader-budget/config.example.json` to `trader_in/config.json` in
   whatever directory you'll run the pipeline from, and set your own budget,
   max loss per trade and the other limits. Everything else bootstraps
   itself (a fresh `trader_in/portfolio.json` is assumed from the config's
   budget if none exists).
3. Run `trader-scanner universe` once (and again every week or so) to build
   `trader_in/universe.txt`.
4. Work the pipeline in order, left to right, every trading day.

## Data sources — solid vs. flaky, honestly

| source | used by | reliability |
| --- | --- | --- |
| Yahoo chart/spark JSON (`.NS`/`.BO`) | scanner, entry, exit, timemachine, budget (via alerts), alerts, news (price move) | **Solid.** Free and unofficial, but consistently returned usable OHLCV in every test during development, including intraday 5-minute bars and 60 days of history. |
| NSE's `archives.nseindia.com/content/equities/EQUITY_L.csv` | scanner (universe) | **Solid** — the static archive host, as opposed to `nseindia.com`'s live API, has not shown rate-limiting in testing. |
| NSE's live `nseindia.com/api/corporate-announcements` | news | **Best-effort.** This is NSE's live, bot-defended API. It worked in testing after a session-cookie priming step, but is the one piece of this pack most likely to be rate-limited or blocked outright, especially from outside India or a data-center IP. Fails soft, per symbol, never crashes a run. |
| NSE's live `nseindia.com/api/quote-equity` (circuit/price-band) | risk (circuit-limit check) | **Flaky — blocked in every test.** Returned HTTP 403 consistently, even with the same cookie-priming approach that worked for corporate-announcements. trader-risk treats an unreachable band as *unknown*, never as *no band*, and prints an explicit warning asking you to verify manually rather than silently skipping the check. |
| Yahoo Finance news search (`v1/finance/search`) | news (fallback) | **Thin.** A bare-symbol search is a fuzzy text match across Yahoo's whole corpus (mostly US stories); this pack keeps only items whose own `relatedTickers` names the exact `.NS` ticker, so expect very few hits. |
| Stooq | *(not used)* | Tried for trader-alerts; answered 404 for every NSE ticker. Dropped — the US pack's Stooq fallback has no equivalent here. |

A known Yahoo data gap worth knowing about: the 60-day, 5-minute history for
NSE tickers has no usable OHLC for the last 10 minutes of every session
(15:20–15:30 IST) — see trader-timemachine's `SKILL.md`. It does not affect
any other skill, which only ever use the current day's bars as they arrive.

## How this differs from the Alpaca (US) pack

- **No live broker integration, anywhere, by design.** The US pack connects
  to Alpaca (or Interactive Brokers) and can place real paper or live
  orders behind three gates (a risk PASS, a chat yes, and Claude's own
  tool-permission card). This pack stops one gate earlier: trader-broker
  produces a ticket and the exact fields for a generic Indian broker's order
  form (`tradingsymbol`, `transaction_type`, `product: CNC`,
  `trigger_price`), and you enter it yourself at Zerodha Kite, Groww, Paytm
  Money or wherever you trade. Nothing in this pack can place an order.
- **Market and calendar.** NSE cash session 09:15–15:30 IST (vs NYSE's
  9:30–4:00 ET), a 2026-only holiday list (with explicit caveats on movable
  festival dates), and no daylight saving at all — India keeps a single
  fixed UTC+5:30 offset year-round, which removes an entire category of
  clock-math bugs the US version has to handle.
- **No PDT rule.** The US pattern-day-trader restriction has no Indian
  equivalent and is not modeled.
- **Universe and asset checks.** NSE's own `EQUITY_L.csv`, filtered to the
  `EQ` series — `BE`/`BZ` trade-for-trade symbols are excluded because they
  legally cannot be day-traded (no intraday netting), which has no US
  equivalent either.
- **Money and sizing.** All amounts in INR. Positions are sized as plain
  CNC (cash/delivery) value — no leverage, no F&O lot sizes, no SPAN or
  exposure margin. That is a deliberate scope cut, not an oversight (see
  trader-budget's `SKILL.md`).
- **Prices round to NSE's ₹0.05 tick size**, not the US cent.
- **A circuit-limit check** replaces the US pack's options/margin/crypto
  checks (none of which apply here, since this pack never models any of
  them) — NSE's daily price bands mean a stop can be un-fillable in a way
  that has no NYSE equivalent.
- **No Pelosi-style tracker and no EDGAR-based earnings analyst** — see
  above and trader-news's `SKILL.md` for why, rather than shipping a weak
  substitute.

## Safety

- **Education and tooling, not financial advice**, throughout. A PASS or a
  matched setup is a rules check, never a recommendation.
- **Paper/manual only — there is no live trading path in this pack at all.**
  trader-risk still hard-BLOCKs a bad entry before it is ever shown as a
  ticket; an exit is never hard-blocked, only warned.
- Every number printed comes from a config file, an order, a quote or a
  computed rule. Nothing is guessed, and nothing promises a winning trade.
- If you want this to actually place orders, you would need to build and
  wire up a live broker connection yourself (for example against Kite
  Connect) — that is explicitly not included here.
