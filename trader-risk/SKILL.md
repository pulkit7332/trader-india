---
name: trader-risk
description: >-
  Risk Manager (India). Checks every NSE/BSE order against your limits and
  stops anything over them, before it ever reaches a broker ticket. For an
  entry: a stop is required, the loss at the stop must fit your max loss per
  trade, reward:risk must meet your minimum, the day's total risk must fit
  your daily loss limit, no more than your trades per day, and no new
  entries outside your entry window or when NSE is closed. It also checks
  the symbol against the real NSE equity list, blocks any short sale (this
  pack never models shorting), and best-effort checks a stop against NSE's
  daily circuit (price-band) limits. There is no PDT rule -- India has no
  such restriction. Prints PASS or BLOCK with the reason. Getting out is
  never blocked by a day limit. Use when the user says "check my order", "is
  this safe", "risk check", "does this break my rules", "can I take this
  trade", "how much have I risked today", or before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk (India)

The Risk Manager is the gate between a proposal and a broker ticket. Every
order written by trader-budget passes through it. A BLOCK is final for that
order: it does not go to the user for a yes, and it does not become a
ticket. Most people who day trade lose money. These limits keep each loss
and each day's losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader_in/ files; writes trader_in/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-29 10:15"  # pin the clock (IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader_in/orders.json` | the proposed orders (trader-budget) |
| `trader_in/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader_in/universe.txt` | the real NSE symbol list (trader-scanner `universe`), for the asset check |
| `trader_in/portfolio.json` | cash and holdings; if missing, a fresh account funded from the budget |
| `trader_in/prices.json` | a fresh price raises the entry it measures risk from |
| `trader_in/state/broker.json` | what was placed today: duplicates, trades per day, daily loss |
| `trader_in/state/risk.json` | the verdicts it writes, fingerprinted per exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format and asset only. Every
other rule turns into a warning for an exit, never a BLOCK.

## The checks

| check | blocks when |
| --- | --- |
| format | no symbol, side not buy/sell, qty not a positive whole number, a product other than `CNC` (this pack never models leverage) |
| asset | (if `trader_in/universe.txt` exists) the symbol is not a real NSE cash-equity ticker |
| short | a sell of more than you hold -- always, this pack never allows short sales |
| blocked | (entries) the ticker is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the budget |
| cash | (entries) the buy would need more cash than `trader_in/portfolio.json` has (no margin) |
| stop | (entries) no stop while `require_stop` is true; a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop) over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) today's entries' max losses, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before`, after `no_entries_after`, or NSE is closed |
| duplicate | (entries) the same symbol and side already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |
| circuit | (entries, best-effort) the stop sits outside today's NSE price band |

There is **no PDT (pattern day trader) check** in this pack. That is a US
FINRA/SEC rule about trading on margin with under $25,000 in an account; it
has no Indian equivalent, so this pack does not model it at all (equivalent
Indian constraints are about margin/leverage product rules per broker, which
this pack sidesteps entirely by sizing CNC/delivery only -- see trader-budget).

## The NSE/BSE calendar and clock

Regular session **09:15 a.m. to 3:30 p.m. IST**, Monday to Friday, closed on
these cash-equity holidays for **2026 only** (per a third-party aggregator,
since NSE's own holiday-circular page could not be fetched reliably during
testing -- treat movable Hindu/Islamic festival dates especially as
best-effort and re-verify against NSE's own circular, published every
December for the coming year, before trusting this list for real trading):

Republic Day (Jan 26), Chhatrapati Shivaji Maharaj Jayanti (Feb 19),
Id-Ul-Fitr/Eid (Mar 31), Ram Navami (Apr 2), Good Friday (Apr 10), Dr. Baba
Saheb Ambedkar Jayanti (Apr 14), Maharashtra Day (May 1), Id-Ul-Adha/Bakri Id
(Jun 27), Independence Day (Aug 15), Ganesh Chaturthi (Aug 27), Gandhi
Jayanti/Dussehra (Oct 2), Diwali Laxmi Pujan (Oct 20 -- cash market closed,
with a special one-hour Muhurat Trading session in the evening that this
pack does not scan), Diwali Balipratipada (Oct 21), Guru Nanak Jayanti (Nov
5), Christmas (Dec 25).

India keeps one fixed offset, **UTC+5:30, all year** -- there is no daylight
saving to account for, unlike the NYSE version of this tool, and NSE's
regular cash-equity calendar has **no scheduled early closes** (Muhurat
trading is a separate evening session on an otherwise-closed day, not an
early close of the regular session). Outside 2026 this pack's calendar is
unknown.

## The circuit-limit check, honestly

NSE sets a daily price band (commonly 2%, 5%, 10% or 20%, varies by stock)
outside which a stock simply cannot trade that session. A stop placed beyond
that band cannot realistically fill. This pack tries to fetch the live band
from NSE's `quote-equity` API -- but unlike the `corporate-announcements`
feed trader-news uses, this endpoint **blocked plain scripted requests in
every test during development** (HTTP 403), even with the same session-cookie
priming that worked elsewhere. When it is unreachable, this check prints a
warning, never a silent pass: **"circuit-limit check skipped... verify
yourself."** Treat that warning as real: check the band on your broker's own
terminal before trusting a wide stop to fill.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote RELIANCE --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop, reward:risk, and how much of today's limits it uses. For a
   BLOCK, give the reason and the fix (fewer shares, a closer stop from
   trader-exit, wait for the window, or skip the trade). Never change a
   limit yourself.
4. If the order changes in any way, run the check again.

## Rules

- A PASS only means the order is allowed to be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the broker log.
- Free Yahoo prices are unofficial and can lag; a stop can fill worse than its price in a fast market or on a circuit day.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as the user's yes.

## Differences from the Alpaca (US) pack

- NSE/BSE calendar (09:15-15:30 IST, the holidays above) instead of NYSE's,
  no daylight saving, and no PDT check.
- Symbol validation against NSE's own `EQUITY_L.csv`-derived universe
  instead of a "US stock or ETF" asset check.
- An India-specific circuit-limit check in place of the US pack's options/
  margin/crypto checks, which do not apply since this pack never models any
  of those products.
- All amounts in INR.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper/manual only. **This pack has no live broker integration of any
  kind** -- not Kite Connect, not any other API. trader-broker only prints
  an order ticket for you to enter yourself at your own broker.
