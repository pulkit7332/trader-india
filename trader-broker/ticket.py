#!/usr/bin/env python3
"""Order Ticket printer for NSE/BSE trades. Standard library only.

    python3 ticket.py show                     # every ticket, READY or not
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status entered --filled-qty 63 --filled-price 1174.30
    python3 ticket.py result --index 1 --status not_entered --note "spread too wide at the open"
    python3 ticket.py fill --symbol RELIANCE --side sell --qty 63 --price 1196.20
    python3 ticket.py flatten --symbol RELIANCE --qty 63
    python3 ticket.py log

THIS PACK HAS NO LIVE BROKER INTEGRATION OF ANY KIND -- not Kite Connect,
not any other API. `ticket.py` never places an order anywhere. It renders a
plain-language ticket plus a structured JSON order (shaped like a generic
Indian broker's order form: symbol, transaction type, product CNC,
order type, quantity, price, trigger price, validity) for YOU to type into
your own broker's app (Zerodha Kite, Groww, Paytm Money, or any other), then
records what you did so trader-risk can hold the daily limits.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

DATA_DIR = "trader_in"
YES_RE = re.compile(r"^\s*(y|yes|yeah|yep|confirm|confirmed|do it|go ahead)\.?\s*$", re.I)


def ist_to_utc(local_naive):
    return local_naive - datetime.timedelta(hours=5, minutes=30)


def to_ist(utc_naive):
    return utc_naive + datetime.timedelta(hours=5, minutes=30)


def parse_now(text):
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(t)
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return default


def save_json(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)


def fingerprint(order):
    key = json.dumps(order, sort_keys=True)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def orders_path():
    return os.path.join(DATA_DIR, "orders.json")


def risk_path():
    return os.path.join(DATA_DIR, "state", "risk.json")


def broker_path():
    return os.path.join(DATA_DIR, "state", "broker.json")


def load_orders():
    return load_json(orders_path(), {"orders": []}).get("orders", [])


def load_verdicts():
    return load_json(risk_path(), {"verdicts": {}}).get("verdicts", {})


def load_broker():
    return load_json(broker_path(), {"approvals": {}, "entries": [], "log": [], "fills": []})


def load_portfolio():
    existing = load_json(os.path.join(DATA_DIR, "portfolio.json"))
    if existing is not None:
        return existing
    cfg = load_json(os.path.join(DATA_DIR, "config.json"), {})
    budget = cfg.get("budget_inr", 100000)
    buffer_pct = cfg.get("cash_buffer_pct", 10)
    return {"cash_inr": round(budget * (1 - buffer_pct / 100.0), 2), "positions": {}}


def manual_ticket(order):
    plan = order.get("plan", {})
    side_word = "BUY" if order.get("side") == "buy" else "SELL"
    lines = []
    lines.append("%s %s %s, %s, product CNC (cash/delivery), validity DAY"
                  % (side_word, order.get("qty"), order.get("symbol"), (order.get("order_type") or "MARKET").lower()))
    if order.get("order_type") == "LIMIT" and order.get("limit_price"):
        lines.append("  limit price ₹%s" % order["limit_price"])
    if order.get("stop_loss_price"):
        lines.append("  your own stop-loss order to place separately: SELL at ₹%s (trigger), to close if it goes against you"
                     % order["stop_loss_price"])
    if order.get("take_profit_price"):
        lines.append("  your own target order to place separately: SELL limit ₹%s" % order["take_profit_price"])
    if plan.get("max_loss") is not None:
        lines.append("  max loss about ₹%.2f if the stop fills at ₹%s (it can fill worse in a fast market or on a circuit day)"
                     % (plan["max_loss"], order.get("stop_loss_price")))
    if order.get("why"):
        lines.append("  why: %s" % order["why"])
    return "\n".join(lines)


def structured_order(order):
    """A generic Indian-broker order-form shape (Kite/Groww/Paytm Money all
    take roughly this). Never sent anywhere -- for you to copy into your
    broker's app yourself."""
    return {
        "tradingsymbol": order.get("symbol"), "exchange": "NSE",
        "transaction_type": (order.get("side") or "").upper(),
        "order_type": order.get("order_type", "MARKET"),
        "product": order.get("product", "CNC"), "validity": "DAY",
        "quantity": order.get("qty"), "price": order.get("limit_price"),
        "trigger_price": order.get("stop_loss_price"),
    }


def cmd_show(a):
    orders = load_orders()
    verdicts = load_verdicts()
    broker = load_broker()
    today = to_ist(parse_now(a.now)).date().isoformat()
    if not orders:
        print("No trader_in/orders.json; run trader-budget first.")
        return 1
    any_ready = False
    for i, order in enumerate(orders, 1):
        fp = fingerprint(order)
        v = verdicts.get(fp)
        approved = broker.get("approvals", {}).get(fp)
        print("ORDER %d of %d, PAPER/MANUAL -- no live broker in this pack" % (i, len(orders)))
        print(manual_ticket(order))
        if not v or v.get("date") != today:
            print("  NOT READY: no trader-risk PASS for this exact order today. Run /trader-risk first.")
        elif v.get("verdict") != "PASS":
            print("  BLOCKED by trader-risk: fix the order and re-check, do not show this to the user as ready.")
        elif approved:
            print("  APPROVED: you said %r at %s. Copy the order below into your broker, then record the result."
                  % (approved["reply"], approved["at"]))
            any_ready = True
        else:
            print("  READY: trader-risk PASS, waiting for your yes.")
            any_ready = True
        print("  Enter this yourself at your broker (Kite / Groww / Paytm Money / etc.):")
        print("    %s" % json.dumps(structured_order(order)))
        print()
    return 0 if any_ready else 1


def cmd_approve(a):
    orders = load_orders()
    if a.index < 1 or a.index > len(orders):
        print("No order #%d." % a.index)
        return 1
    order = orders[a.index - 1]
    fp = fingerprint(order)
    verdicts = load_verdicts()
    today = to_ist(parse_now(a.now)).date().isoformat()
    v = verdicts.get(fp)
    if not v or v.get("date") != today or v.get("verdict") != "PASS":
        print("Not recorded: order #%d has not passed trader-risk today." % a.index)
        return 1
    if not YES_RE.match(a.reply or ""):
        print("Not recorded: %r is not a plain yes for this one order." % a.reply)
        return 1
    broker = load_broker()
    broker.setdefault("approvals", {})[fp] = {"reply": a.reply, "at": to_ist(parse_now(a.now)).strftime("%Y-%m-%d %H:%M"), "order": order}
    save_json(broker_path(), broker)
    print("Recorded your yes for order #%d (%s %s %s). Enter it at your broker now -- nothing here places it."
          % (a.index, order.get("side"), order.get("qty"), order.get("symbol")))
    return 0


def cmd_result(a):
    orders = load_orders()
    if a.index < 1 or a.index > len(orders):
        print("No order #%d." % a.index)
        return 1
    order = orders[a.index - 1]
    fp = fingerprint(order)
    broker = load_broker()
    if a.status == "entered" and fp not in broker.get("approvals", {}):
        print("Refused: no recorded yes for order #%d. Run `ticket.py approve` first." % a.index)
        return 1
    now_utc = parse_now(a.now)
    today = to_ist(now_utc).date().isoformat()
    entry = {"date": today, "at": to_ist(now_utc).strftime("%H:%M"), "index": a.index,
             "symbol": order.get("symbol"), "side": order.get("side"), "status": a.status,
             "qty": a.filled_qty or order.get("qty"), "price": a.filled_price, "note": a.note}
    broker.setdefault("log", []).append(entry)
    if a.status == "entered" and order.get("side") == "buy":
        plan = order.get("plan", {})
        broker.setdefault("entries", []).append({"date": today, "symbol": order.get("symbol"), "side": "buy",
                                                   "max_loss": plan.get("max_loss", 0)})
        portfolio = load_portfolio()
        qty = int(a.filled_qty or order.get("qty"))
        price = a.filled_price or plan.get("entry")
        portfolio.setdefault("positions", {})[order["symbol"]] = portfolio.get("positions", {}).get(order["symbol"], 0) + qty
        if price:
            portfolio["cash_inr"] = portfolio.get("cash_inr", 0) - qty * price
        save_json(os.path.join(DATA_DIR, "portfolio.json"), portfolio)
    save_json(broker_path(), broker)
    print("Recorded: order #%d %s (%s)." % (a.index, a.status, a.note or "no note"))
    return 0


def cmd_fill(a):
    now_utc = parse_now(a.now)
    broker = load_broker()
    broker.setdefault("fills", []).append({"at": to_ist(now_utc).strftime("%Y-%m-%d %H:%M"), "symbol": a.symbol.upper(),
                                            "side": a.side, "qty": a.qty, "price": a.price})
    save_json(broker_path(), broker)
    portfolio = load_portfolio()
    pos = portfolio.setdefault("positions", {})
    sign = -1 if a.side == "sell" else 1
    pos[a.symbol.upper()] = pos.get(a.symbol.upper(), 0) + sign * a.qty
    portfolio["cash_inr"] = portfolio.get("cash_inr", 0) - sign * a.qty * a.price
    save_json(os.path.join(DATA_DIR, "portfolio.json"), portfolio)
    print("Recorded fill: %s %d %s at ₹%.2f." % (a.side, a.qty, a.symbol.upper(), a.price))
    return 0


def cmd_flatten(a):
    plan = load_json(os.path.join(DATA_DIR, "plan.json"), {})
    order = {"symbol": a.symbol.upper(), "side": "sell", "qty": str(a.qty), "order_type": "MARKET",
             "product": "CNC", "validity": "DAY", "limit_price": None, "stop_loss_price": None,
             "take_profit_price": None, "why": "flatten by %s: close the position, win or lose, no overnight hold"
             % plan.get("flat_by", "flat-by time"), "plan": {}}
    save_json(orders_path(), {"orders": [order]})
    print("Wrote a flatten order: sell %d %s, market, CNC. Run /trader-risk, then your yes, then enter it at your broker." % (a.qty, a.symbol.upper()))
    return 0


def cmd_log(a):
    broker = load_broker()
    for e in broker.get("log", []):
        print("%s %s  order #%s  %s %s %s  %s" % (e.get("date"), e.get("at"), e.get("index"), e.get("status"),
                                                     e.get("side"), e.get("symbol"), e.get("note") or ""))
    for f in broker.get("fills", []):
        print("%s  fill  %s %s %s @ ₹%s" % (f.get("at"), f.get("side"), f.get("qty"), f.get("symbol"), f.get("price")))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE manual order ticket (no live broker)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("show")
    ps.add_argument("--now")
    ps.set_defaults(func=cmd_show)

    pa = sub.add_parser("approve")
    pa.add_argument("--index", type=int, required=True)
    pa.add_argument("--reply", required=True)
    pa.add_argument("--now")
    pa.set_defaults(func=cmd_approve)

    pr = sub.add_parser("result")
    pr.add_argument("--index", type=int, required=True)
    pr.add_argument("--status", choices=["entered", "not_entered"], required=True)
    pr.add_argument("--filled-qty", type=int)
    pr.add_argument("--filled-price", type=float)
    pr.add_argument("--note")
    pr.add_argument("--now")
    pr.set_defaults(func=cmd_result)

    pf = sub.add_parser("fill")
    pf.add_argument("--symbol", required=True)
    pf.add_argument("--side", choices=["buy", "sell"], required=True)
    pf.add_argument("--qty", type=int, required=True)
    pf.add_argument("--price", type=float, required=True)
    pf.add_argument("--now")
    pf.set_defaults(func=cmd_fill)

    pl = sub.add_parser("flatten")
    pl.add_argument("--symbol", required=True)
    pl.add_argument("--qty", type=int, required=True)
    pl.set_defaults(func=cmd_flatten)

    plg = sub.add_parser("log")
    plg.set_defaults(func=cmd_log)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
