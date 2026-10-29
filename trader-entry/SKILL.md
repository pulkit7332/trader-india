---
name: trader-entry
description: >-
  Entry Timer (India). Watches an NSE/BSE setup bar by bar and tells you the
  moment to get in. Reads free Yahoo 1-minute or 5-minute bars for `.NS` /
  `.BO` tickers, builds the opening range (the first 15 minutes of the
  09:15-15:30 IST session by default) and calls the entry on the first
  completed bar that CLOSES above the range high, or above a level you give
  it, with an optional volume check. Says waiting and how far, triggered
  with the time and the price, expired, or not open yet, computed in IST
  with no daylight saving. Writes trader_in/entry.json for /trader-exit.
  Long side only. It never places an order. Use when the user says "tell me
  when to get in", "watch RELIANCE", "has it broken out", "did it break the
  opening range", "entry signal", "time my entry", "check my watchlist", or
  after /trader-scanner and /trader-news, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# trader-entry (India)

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the level in the middle of a bar is not an entry. A bar
that CLOSES above it is. Then it says the time and the price, writes them
down for the Exit Planner, and stops. It never buys anything.

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE                 # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write         # also save trader_in/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1180.00
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --interval 1m --min-volume-x 1.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 15:15   # one line per closed bar, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                      # every hit in trader_in/watchlist.json
```

Other flags: `--or-minutes N` (default 15), `--json`, `--offline FILE` (a
saved Yahoo chart JSON, or a folder of `<SYMBOL>.json`), `--now "2026-10-29
10:15"` (IST) to replay a day. `watch` with `--now` or `--offline` runs on a
simulated clock, stepping one bar at a time, and replays the day without
waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader_in/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| Yahoo chart API | today's bars, `range=1d&interval=5m` (or `1m`): free, unofficial, can lag |
| `trader_in/entry.json` | with `check --write`, or when `watch` triggers: `symbol`, `setup`, `status`, `price`, `time_ist`, `bar`, `range_high`, `range_low`, `date` |

## The rule

- **Opening range (orb).** The high and low of the first `--or-minutes` (15)
  of the NSE cash session: the 9:15, 9:20 and 9:25 five-minute bars. The
  level to beat is the range high.
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **Only closed bars count.** A bar is closed once the clock (real, or
  `--now`) has passed its start plus 5 (or 1) minutes. Yahoo's own
  in-progress bar is never used, and a `--now` set in the past clips out any
  bar that has not happened yet by that time — this is what makes `--now`
  safe for reviewing a past morning without accidentally looking ahead.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the range bars' average.
- **Time limits.** The NSE cash session is 09:15-15:30 IST, Monday to Friday,
  closed on the holidays listed in trader-risk's SKILL.md. India keeps one
  fixed UTC+5:30 offset all year — there is no daylight-saving adjustment to
  make, unlike the NYSE version of this tool.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, a holiday/weekend, or no data. Before 9:15: not open
  yet. Before the range closes: still forming.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line:
   waiting and how far, triggered at what time and price, or expired.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 15:00` in
   the background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. `watch` wakes just after each bar closes and exits 0 on the
   trigger (it writes `trader_in/entry.json`) or 1 when the setup expires.
4. When it triggers, say the time and the price, and that nothing was placed.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Rules

- Bar by bar, on free Yahoo 1-minute or 5-minute bars for `.NS`/`.BO`
  tickers. They are unofficial and can lag. Say so when a minute matters.
- A signal is information, not an order, and not a promise.
- Long side only. If the user wants a short sale, say that this pack plans
  longs only (and that short selling intraday carries its own NSE margin and
  securities-lending rules this pack does not model).
- One trigger per setup per day: the first close above. Later closes do not
  reset it.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/trader-exit`), never moves the level after the fact, and never counts a
bar that has not closed. It never runs `/trader-broker` from a background
watch: nobody is there to say yes.

## Differences from the Alpaca (US) pack

- NSE's session (09:15-15:30 IST) has no daylight-saving wrinkle and no
  1:00 p.m. early closes to track, unlike NYSE's ET clock.
- Tickers are bare NSE symbols (`RELIANCE`); `.NS` is appended for every
  Yahoo lookup automatically (`.BO` for BSE tickers you type explicitly).

## Safety

- Education and tooling, not financial advice. A trigger is a rules match,
  not a recommendation.
- Paper/manual only. This pack has no live broker integration of any kind —
  trader-broker only prints an order ticket for you to enter yourself.
