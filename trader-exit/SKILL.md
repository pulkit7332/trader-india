---
name: trader-exit
description: >-
  Exit Planner (India). Sets your stop and your target before you enter an
  NSE/BSE day trade, so you know when to get out. Reads the entry from
  trader-entry (or a price you give), puts the stop at the opening-range
  midpoint by default (or the range low, your price, a percent, or a
  multiple of the ATR), rounds it to the NSE tick size (Rs 0.05 for cash
  equities), and sets the target at 2R (or 1R, or your price). Refuses a
  stop at or above the entry, a target at or below it, or a risk too tight
  to survive the spread, and says why. Writes trader_in/plan.json for the
  Time Machine, the Budget Manager and Price Alerts. Long side only. Use
  when the user says "where's my stop", "set my stop and target", "plan the
  exit", "when do I get out", "stop loss", "what's my R", "risk reward", or
  right after /trader-entry triggers, before /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X]"
---

# trader-exit (India)

The Exit Planner decides how a trade ends before it starts. The stop is
where the setup has failed. The target is where you take what you came for.
Both are set BEFORE the entry, written down and handed on. A stop is a
plan, not a guarantee: a gap or a fast move can fill past it, and NSE's own
daily circuit limits (see trader-risk) can make a stop unreachable on a
circuit day regardless of what this plan says.

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader_in/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo or --offline)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1174.30 --or-high 1170.50 --or-low 1156.20
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader_in/plan.json
```

Other flags: `--flat-by 15:15`, `--json`, `--now "2026-10-29 10:15"` (IST),
`--entry PRICE` with a waiting entry.json to plan ahead, `--offline FILE` (a
saved Yahoo chart JSON for an ATR stop).

## What it reads and writes

| file | what |
| --- | --- |
| `trader_in/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| Yahoo chart API | only for an `atr:K` stop: today's bars up to the entry bar, free and unofficial |
| `trader_in/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on today's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **Rounding — the NSE tick size.** NSE cash equities trade in Rs 0.05 ticks
  (not the US pack's cent). The stop rounds AWAY from the entry to the tick
  (down, for a long). Risk = entry minus stop. Target = entry + R x risk,
  rounded to the nearest tick, half up. `2R` by default; `1R`, `1.5R` or
  `price:X` also work.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price: too tight to survive the
  spread. An entry.json that has not triggered, or is from another day
  (unless you give `--symbol`/`--entry` directly). A short: this pack plans
  longs only.
- **Flat by.** 15:15 IST by default (`--flat-by`, or `flat_by` in
  `trader_in/config.json`) — 15 minutes before the 15:30 close, which also
  keeps you clear of any broker's own auto-square-off cutoff for intraday
  (MIS/CNC-intraday) positions, which several Indian brokers run at 15:15 or
  15:20. Out by then, win or lose, no overnight hold. An entry at or after
  the flat-by time is refused.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk and flat by.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee — and that a stop placed
   beyond the day's circuit limit cannot realistically fill (trader-risk
   checks this before any order is shown).
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It
  never prints a P&L or a return.
- Long side only.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

## Differences from the Alpaca (US) pack

- Rounds to the NSE tick size (Rs 0.05) instead of the cent.
- Default flat-by is 15:15 IST (15 minutes before NSE's 15:30 close, and
  ahead of most Indian brokers' own MIS auto-square-off window), not 15:55 ET.
- Amounts are in INR throughout.

## Safety

- Education and tooling, not financial advice.
- Paper/manual only. This pack has no live broker integration of any kind —
  trader-broker only prints an order ticket for you to enter yourself at
  Zerodha Kite, Groww, Paytm Money or wherever you trade.
