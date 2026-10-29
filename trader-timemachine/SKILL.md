---
name: trader-timemachine
description: >-
  Time Machine (India). Replays every possible outcome of an NSE/BSE day
  trade before you risk a rupee: 10,000 simulated sessions plus every past
  time the setup fired. Takes the plan from trader-exit (entry, stop,
  target), rebuilds the rest of the day 10,000 times from the stock's own
  5-minute bars over the last ~60 sessions, and re-runs the same
  opening-range rule on every past session. Prints the odds of target first,
  stop first, or neither by the cutoff, where the price sits then, the
  average R, the history, and one honest verdict, then hands off to
  /trader-budget. Use when the user says "run the time machine", "replay
  this trade", "what are the odds", "simulate this trade", "monte carlo",
  "backtest this setup", "how often does this hit the target", "is this a
  coin flip", or after /trader-exit writes a plan, before /trader-budget.
argument-hint: "[path to plan.json, or SYMBOL]"
---

# trader-timemachine (India)

The Time Machine sits between the plan and the money. trader-exit has set
the entry, the stop and the target. Before anything is sized or sent, this
skill asks one question: if the rest of today plays out like the stock's own
last ~60 sessions, how often does this plan hit the target first, the stop
first, or neither? It answers with 10,000 replays and with the real record
of the same rule on every past session. It is a simulation built from the
stock's own recent bars, not a prediction.

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader_in/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader_in/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:15      # end each replay at your flat-by time
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-10-29 \
    --entry 1174.30 --stop 1163.35 --target 1196.20 --entry-bar 4
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved Yahoo chart JSON), `--now "2026-10-29
10:15"` (IST) to pin the clock.

## What it reads and writes

| file | what |
| --- | --- |
| `trader_in/plan.json` | the plan from trader-exit: symbol, date, entry, stop, target, entry_bar |
| Yahoo chart API | one request: 60 days of 5-minute bars (`range=60d&interval=5m`), free and unofficial |
| `trader_in/timemachine.json` | with `--write`: `{plan, mc, history, verdict}` |

## A real data gap, and how this tool works around it

Yahoo's 60-day 5-minute history for NSE tickers consistently has **no usable
OHLC for the last two 5-minute slots of every session (15:20-15:30 IST)** —
the closing-auction print lands as a single snapshot price instead of a real
bar. This is not a transient flakiness; it showed up on every session tested.
So the pool and the replay both stop at bar 73 of 75 (the 15:00-15:05 bar is
the last one used), and a plan whose entry triggered after about 15:05 has
too little of the session left to replay and is refused with a plain reason.
This does not affect trader-scanner, trader-entry or trader-exit, which only
ever use the current day's own bars as they arrive live.

## How it works

1. **The pool.** Only FULL sessions before the plan's date (75 bars on the
   5-minute grid, of which 73 are usable per the gap above). Every bar
   becomes four ratios to the bar before it: open, high, low and close over
   the previous close.
2. **One replay.** Start at the entry price. For each bar left in the usable
   session, pick a past bar from the same time of day (plus or minus
   `--window`, 2 bars) at random, scale it to the current price, and check
   the stop then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give four odds: target first, stop first, neither
   (still open at `--until`, default the 15:00-15:05 cutoff), and green
   (target first, or above the entry at the cutoff). R is +1 at a 1R target,
   -1 at the stop (a gap through the stop still counts as -1R here — a real
   fill can be worse), and otherwise the distance from the entry at the
   cutoff divided by the risk.
4. **The history** re-runs a comparable opening-range rule (same opening
   range, a stop at the same rule family — midpoint or low — and the same
   target R) on every one of those past sessions. That is what really
   happened, not a simulation.
5. **The verdict** looks only at the replays that hit one side, and asks how
   often the target came first against the break-even rate for that R: 50%
   at 1R, 33% at 2R, 25% at 3R. Within 5 points: a coin flip or break-even.
   5-15 points: leans one way. 15 or more: strongly one way.

**The seed.** Draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it.

## Steps

1. Make sure `trader_in/plan.json` is today's plan from `/trader-exit`. If
   there is none, run that first, or pass the plan as flags.
2. Run `timemachine.py run --write`. Add `--until 15:15` to match your
   flat-by time.
3. Tell the user, in this order: the four odds, the median and 5-95 range of
   the price at the cutoff, the average R, the history line, then the
   verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly.
5. Say once that this is built from the last ~60 sessions and is not a
   prediction. Check `/trader-news` for the stock before going on, and keep
   in mind that NSE's own daily circuit limits (trader-risk) are a real
   floor/ceiling these ~60 trading days may never have actually tested.
6. If the user still wants the trade, hand off to `/trader-budget` to size it.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- Free Yahoo data is unofficial and can lag. Always say the seed.
- One plan per run. A changed entry, stop, target or date is a new run.

## What it never does

It never places an order, never sizes a position, never changes the plan,
and never calls a result a sure thing. It never prints a P&L or a profit. It
never uses a bar past the 15:20-15:30 gap described above, and never mixes
today's own bars into the historical pool.

## Differences from the Alpaca (US) pack

- The data-gap workaround above (Yahoo's NSE 5-minute history drops the last
  10 minutes of every session) has no equivalent in the US pack, whose
  5-minute history for US tickers runs cleanly through the 4:00 p.m. close.
- Session length is 73 usable bars (of a 75-bar, 09:15-15:30 IST grid)
  instead of NYSE's 78 bars, 9:30 a.m.-4:00 p.m. ET.
- Amounts are in INR throughout.

## Safety

- Education and tooling, not financial advice.
- Paper/manual only. This pack has no live broker integration of any kind.
