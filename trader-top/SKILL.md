---
name: trader-top
description: >-
  Top Setups (India). One command that rescans NSE for opening-range
  breakouts and ranks the ones you could still enter right now, by a fixed,
  printed rule: keeps only charts still holding above their range high with
  a stop (range midpoint) within 3% of the price, scores them on the day's
  move plus distance above the range, adds a bonus for a fresh NSE earnings,
  corporate-action or deal filing, then runs trader-exit and the
  10,000-replay trader-timemachine on the best 10 and orders them by
  average R. Reuses the pack's own scanner, news, exit and time-machine
  scripts, so every number matches what those skills print. Writes
  trader_in/top.json. Use when the user says "top stocks", "best setups
  right now", "where can I take entry", "rank the breakouts", "what's
  worth a look now", "refresh the list", or "/trader-top". The user still
  picks; a rank is a rules output, not a recommendation.
argument-hint: "[--no-rescan] [--tm-top N] [--max-risk-pct P]"
---

# trader-top (India)

The scanner finds every chart that matched a rule. On a broad up day that
can be 180 charts, which is not a list anyone can act on. trader-top
narrows it with one fixed, printed rule and replays what is left through
the Time Machine, fresh every time it runs. It still never picks for the
user. It shows a ranked table with the rule beside it, and the user picks.

`${CLAUDE_SKILL_DIR}` below is this skill's folder. It must sit beside the
other `trader-*` folders of this pack (it runs their scripts).

```bash
python3 "${CLAUDE_SKILL_DIR}/top.py"                          # rescan, score, replay the best 10
python3 "${CLAUDE_SKILL_DIR}/top.py" --no-rescan              # reuse today's trader_in/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/top.py" --tm-top 5 --max-risk-pct 2
python3 "${CLAUDE_SKILL_DIR}/top.py" --now "2026-11-04 10:30" # replay a past moment of today
python3 "${CLAUDE_SKILL_DIR}/top.py" --json
```

Other flags: `--min-price 20`, `--filings-top 20`, `--max-confirm 200`
(passed to the scanner), `--flat-by 15:15`, `--replays 10000`, `--seed 19`.
A full run with a rescan takes about 4 minutes. `--no-rescan` takes under a
minute. Exit code 0 when something was ranked, 1 otherwise.

## The rule

| stage | what | from |
| --- | --- | --- |
| 1 | every chart that closed a 5-minute bar above its 15-minute opening range | `trader-scanner orb` |
| 2 | price now = close of the last fully closed 5-minute bar. Keep it only if it is above the range high, at least ₹20, and the range-midpoint stop is no more than `--max-risk-pct` (3%) below it. fast score = day move % + % above the range high | Yahoo spark, 20 a request |
| 3 | best `--filings-top` (20) by fast score: +2 for an `earnings`, `corporate-action` or `deal` NSE filing in the last 48 hours | `trader-news --no-yahoo` |
| 4 | best `--tm-top` (10): entry at the price now, stop at the range midpoint, target 2R, flat by 15:15, replayed 10,000 times (seed 19) from the stock's own last ~60 sessions | `trader-exit plan`, `trader-timemachine run` |
| order | average R, then target-first odds | |

Average R is the ranking key, not target-first odds, because it also
counts the replays that hit neither side, valued at their cutoff price.
Late in the session almost every replay hits neither side, so
target-first odds stop telling the charts apart. The script prints a
warning when most replays hit neither side.

## When it runs

NSE trading days only. It runs from 09:30 IST, once the 15-minute range has
closed. It refuses at or after the flat-by time (15:15), because there is
no session left to plan a new entry into. `--now` pins the clock to an
earlier moment of today for review. The price and the scanner are both
clipped to bars that had closed by then.

## What it reads and writes

| file | what |
| --- | --- |
| `trader_in/watchlist.json` | written by the scanner on a rescan, or read with `--no-rescan` (must be today's `orb` scan) |
| `trader_in/top.json` | `as_of`, the `rule`, the scan counts, how many were dropped and why, `ranked[]` (symbol, price, stop, target, risk %, fast score, filing tags, the Time Machine's `mc`, `history` and `verdict`), `skipped`, `news_errors` |

It does not write `plan.json`. The user picks a chart and then runs
`/trader-exit` for it, the normal way.

## Steps

1. Run `top.py`. Tell the user the funnel in one line: breakouts, how many
   still hold, and how many were dropped (back in range, stop too wide,
   under ₹20).
2. Show the ranked table as it is. Give the reason for each column the
   first time (target / stop / neither are replay odds, history is
   `fired: target/stop/neither` on real past sessions).
3. Point out any `!` warning lines (a halt, risk or regulatory filing),
   and the "most replays hit neither side" warning if it printed.
4. The user picks one. Hand it on: `/trader-entry SYMBOL` to confirm the
   bar, `/trader-exit`, `/trader-budget`, `/trader-risk`, `/trader-broker`.
   Never pick for them, and never call the first row the one to buy.

## Rules

- Say "ranked by the rule", never "best stock", "a buy" or "a winner".
- Never print a P&L, a return or a profit figure. Average R is a simulated
  outcome per unit of risk, and has to be described that way.
- Say the time of the data (IST) and that Yahoo data is delayed. A rank is
  stale once the price has moved, so re-run before acting.
- Report skipped symbols and NSE filing errors. Do not hide them.

## What it never does

It never places, sizes or risk-checks an order. It never writes a plan and
never says which stock to buy. It never claims a rank predicts anything.

## Safety

- Education and tooling, not financial advice.
- Paper/manual only. **This pack has no live broker integration at all.**
