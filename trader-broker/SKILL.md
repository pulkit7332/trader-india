---
name: trader-broker
description: >-
  Order Ticket (India). THIS PACK HAS NO LIVE BROKER INTEGRATION OF ANY
  KIND -- not Kite Connect, not any other API. This skill turns an approved
  order into a plain-language ticket plus the structured fields (symbol,
  transaction type, product CNC, order type, quantity, price, trigger price)
  for YOU to type into your own broker's app -- Zerodha Kite, Groww, Paytm
  Money, or whatever you use. It records your typed yes word for word before
  showing the ticket, and records what you actually did (entered, not
  entered, filled) so trader-risk can hold the daily limits. Use when the
  user says "place the order", "what do I enter at my broker", "show me the
  ticket", "I bought it", "I didn't take it", "close my position", "flatten",
  "I got out", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker (India)

**There is no live broker connection in this pack.** `ticket.py` never calls
any API and never places an order anywhere, on any account, paper or real.
Its only job is to turn a risk-checked order into exactly what you would
type into your own broker's order form, and to keep an honest log of what
you actually did with it, so the other skills (especially trader-risk's
daily limits) stay accurate.

This is a deliberate scope decision for this pack, not a missing feature:
the original Alpaca-based US pack connects to a real paper/live brokerage
API with its own three-gate approval flow (risk PASS, a chat yes, and
Claude's own tool-permission card). Building the same thing against a live
Indian broker (Kite Connect or similar) was explicitly out of scope here.

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status entered --filled-qty 63 --filled-price 1174.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status not_entered --note "spread too wide"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 63 --price 1196.20
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 63
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader_in/orders.json` | the proposed order (trader-budget writes it; `flatten` replaces it) |
| `trader_in/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader_in/state/broker.json` | every yes, entered/not-entered result and fill |
| `trader_in/portfolio.json` | cash and shares, updated by `result --status entered` and `fill` |
| `trader_in/config.json` | the budget, used only to seed a fresh portfolio's starting cash |
| `trader_in/plan.json` | the flat-by time for `flatten` |

## The two gates that still apply

Even with no broker API, this pack keeps the spirit of the original's
multi-gate design:

1. **trader-risk passed this exact order today** (matched by fingerprint) --
   `ticket.py show` will not show a ticket as READY otherwise.
2. **You typed an explicit yes for this one order**, recorded word for word
   by `ticket.py approve`. A vague reply ("sure", "yes to all", a yes that
   names a different stock or amount) is refused with "Not recorded" --
   Claude must ask again, and must never turn a reply into a yes itself.

There is no third gate (a tool-permission card) because there is no tool
call that could place anything -- the only thing that can place this order
is you, in your own broker's app, after reading the ticket.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Enter this at your broker? Reply yes or no." One
   order per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", ask again.
4. After "Recorded your yes", print the structured order fields exactly as
   the ticket shows and tell the user to enter them at their own broker now.
   This skill does nothing further on its own.
5. Once the user says they placed it (or didn't), record it:
   `ticket.py result --index N --status entered --filled-qty Q --filled-price P`,
   or `--status not_entered --note "..."`. This updates `trader_in/portfolio.json`
   and the day's entry count that trader-risk reads.

## Steps: while it is open, and out by the close

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). Since there is no broker connection, YOUR
  OWN separate stop-loss and target orders at your broker do the actual
  stopping -- this pack only tells you when those levels are hit.
- When the stop or the target fills (the user tells you, or you check their
  broker statement together), record it: `ticket.py fill --symbol RELIANCE
  --side sell --qty 63 --price 1196.20`.
- By `flat_by` (15:15 IST by default): `ticket.py flatten --symbol RELIANCE
  --qty 63` writes a market sell to `trader_in/orders.json`. Then
  `/trader-risk` (a sell that only closes what the user holds is never
  blocked by the daily limits or the entry window), then the user's yes as
  above, then they enter it at their broker.

## Example

```
ORDER 1 of 1, PAPER/MANUAL -- no live broker in this pack
BUY 63 RELIANCE, market, product CNC (cash/delivery), validity DAY
  your own stop-loss order to place separately: SELL at ₹1163.35 (trigger), to close if it goes against you
  your own target order to place separately: SELL limit ₹1196.20
  max loss about ₹689.85 if the stop fills at ₹1163.35 (it can fill worse in a fast market or on a circuit day)
  READY: trader-risk PASS, waiting for your yes.
  Enter this yourself at your broker (Kite / Groww / Paytm Money / etc.):
    {"tradingsymbol": "RELIANCE", "exchange": "NSE", "transaction_type": "BUY", "order_type": "MARKET",
     "product": "CNC", "validity": "DAY", "quantity": "63", "price": null, "trigger_price": "1163.35"}
```

The user replies "yes". Claude records it and prints the fields above for
the user to type into their broker's order form themselves. Nothing is sent
anywhere by this skill.

## What it never does

- Never places, modifies or cancels an order anywhere -- there is no API call in this skill at all.
- Never asks for, reads, stores or repeats broker login credentials, API keys or passwords.
- Never promises a fill price: whatever you actually get at your broker is what happened.
- Never turns a vague reply into a yes.

## Differences from the Alpaca (US) pack

- **No broker connection of any kind.** The US pack integrates with Alpaca's
  MCP server (or Interactive Brokers' connector) and actually places orders
  on a paper or live account, gated by trader-risk, the user's chat yes, and
  Claude's own tool-permission card. This pack stops one step earlier: it
  produces the ticket and records what you say you did, but the actual
  order entry happens in your hands, in your own broker's app.
- Order shape is a generic Indian broker order form (`tradingsymbol`,
  `transaction_type`, `product: CNC`, `trigger_price`) instead of Alpaca's
  `place_stock_order` bracket JSON.
- `result` replaces the US pack's `result --status placed/denied/failed`
  with `entered`/`not_entered`, since there is no broker response to record.

## Safety

- Education and tooling, not financial advice.
- **Paper/manual only, always.** There is nothing in this skill, or anywhere
  in this pack, that can place a real order. If you want a live broker
  connection, you would need to build and wire one up yourself (for example
  against Kite Connect) -- this pack deliberately does not include that.
