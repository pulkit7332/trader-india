---
name: trader-alerts
description: >-
  Price Alerts (India). Rings when a price, a stop or the opening bell hits.
  Turns the trade plan into three alerts in one go (the stop, the target and
  "flat by close" at your flat-by time), plus above/below/stop/target
  levels, percent moves from the previous close, any time of day, and NSE's
  09:15 IST opening bell, each ringing once so you are not spammed. Knows
  NSE hours with the 2026 holiday calendar, works out IST itself (no
  daylight saving to account for), fetches free delayed quotes (Yahoo chart
  JSON for `.NS`/`.BO` tickers) one at a time, and writes
  trader_in/prices.json for sizing and the risk check. Use when the user
  says "alert me when", "tell me if RELIANCE hits", "watch my stop", "watch
  my trade", "remind me to close by 3:15", "is the market open", "when does
  the market open", "get me a price", or right after /trader-exit writes a
  plan.
argument-hint: "[ticker] [price]"
---

# trader-alerts (India)

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. Since this pack has no live broker
integration, any stop-loss or target order you placed separately at your own
broker does the actual work; the alerts tell you what happened and when it
is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by close from trader_in/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1163.35
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1196.20
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:15 --label "flat by close"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader_in/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan tickers
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-29 15:15"` pins the clock (IST).

## What it reads and writes

| file | what |
| --- | --- |
| `trader_in/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader_in/alerts.json` | the rules |
| `trader_in/state/alerts.json` | which rules rang and when, so each rings once |
| `trader_in/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader_in/portfolio.json` | the tickers held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close, optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:15") on a trading day | once per trading day |
| `opening_bell` | NSE is open (fires once the clock passes 09:15 IST on a trading day) | once per trading day |

A rule with `"date"` is watched on that date only; `--from-plan` rules carry
the plan's date, so yesterday's stop never rings today.

## Quotes: Yahoo only

`yahoo`: `query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS` (or `.BO`),
an unofficial free JSON endpoint, one request per ticker, falling back to
`query1` on a 429 or 5xx. This is the **one data source** in this skill —
the US pack's Stooq fallback (`stooq.com/q/l/?s=...`) was tried here and
answered 404 for every NSE ticker attempted (Stooq simply does not cover
NSE), so it is not included. If Yahoo is down, there is no automatic
fallback in this skill; read the price from the broker's own app.

Free quotes are unofficial, delayed and can lag. Fine for alerts and for
checking a size, not for timing an order. Each price in
`trader_in/prices.json` keeps its quote time, and trader-risk only uses one
under 15 minutes old.

## The NSE clock

Regular session 09:15 a.m. to 3:30 p.m. IST, closed on weekends and the 2026
holidays listed in trader-risk's SKILL.md (with the same caveat: movable
festival dates there are best-effort, re-verify against NSE's own circular).
India keeps one fixed UTC+5:30 offset all year, so unlike the NYSE version
of this tool there is no daylight-saving calculation and no scheduled early
close to track.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by
   time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the tickers in today's
   rules and rings each alert once.
3. Tell the user what rang in one line each. A stop or target alert means
   your own separately placed stop/target order at your broker has probably
   filled — check your broker's order book and record it with
   `trader-broker fill`. "Flat by close" means close anything still open:
   `trader-broker flatten`, then trader-risk, then the user's yes. Never act
   for them.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: ticker, type, level, day.

## What it never does

It never places, cancels or changes an order (there is none to call), never
claims a quote is live, and never predicts where a price is going.

## Differences from the Alpaca (US) pack

- NSE's 09:15-15:30 IST session, 2026 holiday list, no daylight saving, no
  1:00 p.m. early closes.
- Yahoo chart JSON is the only quote source; Stooq has no NSE coverage.
- Amounts are in INR.

## Safety

- Education and tooling, not financial advice.
- Paper/manual only. This pack has no live broker integration of any kind.
