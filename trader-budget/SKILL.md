---
name: trader-budget
description: >-
  Budget Manager (India). Sizes every NSE/BSE position to your budget and
  your max loss per trade, in plain cash (CNC/delivery) value -- no
  leverage, no F&O lot sizes, no SPAN/exposure margin. Reads the trade plan
  from trader-exit (entry, stop, target), finds the whole shares that fit
  inside four limits (max loss per trade, position cap, max order, cash
  after your buffer), says which limit decided it, and writes ONE order to
  trader_in/orders.json with a stop-loss and take-profit noted for
  trader-risk and the manual ticket trader-broker prints. A stop too far
  away for your max loss is a plain refusal, not a smaller order. Nothing is
  placed. Use when the user says "how many shares", "size this trade", "how
  much should I buy", "I have 1 lakh rupees", "I only want to risk 500 a
  trade", "set my rules", "keep 10% in cash", or after /trader-exit writes a
  plan.
argument-hint: "[budget in rupees]"
---

# trader-budget (India)

The Budget Manager turns a plan ("buy RELIANCE at 1174.30, stop 1163.35")
into an exact order ("buy 63 shares"), inside rules the user sets once. The
stop decides the size: the further away the stop, the fewer shares. It
writes a proposal only. trader-risk checks it and trader-broker turns it
into a manual order ticket for you to enter at your own broker.

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader_in/plan.json + trader_in/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader_in/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write        # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1174.00 --json # a limit order at 1174.00
```

`--now "2026-10-29 10:15"` pins the clock (IST). A plan for another day is
refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader_in/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader_in/plan.json` | the trade: entry, stop, target, flat-by time (trader-exit writes it) |
| `trader_in/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader_in/portfolio.json` | optional: its `cash_inr` caps the size |
| `trader_in/orders.json` | the one proposed order it writes with `--write` |

## Cash (CNC) only -- no leverage, no F&O

This pack sizes **CNC (cash/delivery)** value only: the full price of the
shares, paid in full, no margin. It deliberately does not model intraday
leverage products (Zerodha's MIS, Groww's INTRADAY, or any broker's margin
multiplier) or F&O lot sizes and SPAN+exposure margin -- that is real added
complexity (leverage changes your actual max loss independent of the stop,
and F&O lot sizes are exchange-set and change periodically) that would make
the risk numbers this pack prints misleading if gotten wrong. A same-day buy
and sell on a CNC order is allowed by Indian brokers without any margin
product at all, so "day trading cash equities" works fine on plain CNC; you
simply will not get the extra buying power a leveraged intraday product
would give you. If you want MIS/leveraged sizing, do that math yourself
outside this tool -- it is out of scope here.

## Step 1: set the rules once

If `trader_in/config.json` does not exist, copy `config.example.json` into
`trader_in/config.json` and ask the user for each value in plain words:

- **budget_inr**: how much money this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (default ₹1,000).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the
  budget), the biggest order (₹80,000) and the smallest order (₹500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (₹3,000), **max_trades_per_day**
  (3), **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "15:00") and **flat_by** ("15:15"), all IST.
- **blocked**: tickers they never want to trade.
- Keep **paper** true -- it is a marker in the config for your own tracking; this pack has
  no live broker connection to switch regardless.

## Step 2: size the trade

Whole shares only. The size is the smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop) |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than `trader_in/portfolio.json`'s cash |

The entry it sizes at: the limit price with `--limit`. For a market order,
the plan's entry, or the price in `trader_in/prices.json` if it is fresh
(under 15 minutes old) and higher. A price that ran up since the plan means
a bigger loss at the same stop, so fewer shares.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it, the plan is for another day, or
the ticker is blocked.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees and
the max loss at the stop. Say plainly that a stop is an order, not a
promise: in a fast market, or against NSE's own circuit limits, it can fill
worse than its price or not at all. Then hand over to `/trader-risk`.

## Rules

- Proposals only. This skill never calls a broker (there is none to call).
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes margin,
leverage, F&O or short positions.

## Differences from the Alpaca (US) pack

- Sizes CNC (cash/delivery) only, never a leveraged or margin product, and
  the order object has no Alpaca-specific fields (`order_class: bracket`,
  `time_in_force`) -- it carries a plain `product: "CNC"` and separate
  `stop_loss_price` / `take_profit_price` for trader-risk and trader-broker
  to read, since there is no broker API in this pack to format for.
- All amounts in INR; the example limits (₹1,000 max loss, ₹80,000 max
  order) are sized for a much lower typical per-share price than US tickers.

## Safety

- Education and tooling, not financial advice.
- Paper/manual only. This pack has no live broker integration of any kind --
  trader-broker only prints a ticket for you to enter yourself.
