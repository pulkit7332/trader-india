#!/usr/bin/env python3
"""Budget Manager for NSE/BSE cash (CNC) day trades. Standard library only.

    python3 size.py                        # trader_in/plan.json + trader_in/config.json
    python3 size.py --write                # write trader_in/orders.json
    python3 size.py --limit --write        # a limit order at the plan's entry
    python3 size.py --limit 1174.00 --json
    python3 size.py --now "2026-10-29 10:15"

Sizes to CNC (cash/delivery) value only: whole shares, full price, no
leverage. F&O lot sizes and SPAN+exposure margin are out of scope for this
pack. Nothing is placed -- this writes a proposal only.
"""

import argparse
import datetime
import json
import math
import os
import sys

DATA_DIR = "trader_in"
DEFAULT_CONFIG = {
    "budget_inr": 100000, "cash_buffer_pct": 10, "max_loss_per_trade_inr": 1000,
    "max_position_pct": 75, "max_order_inr": 80000, "min_order_inr": 500,
    "max_daily_loss_inr": 3000, "max_trades_per_day": 3, "min_reward_risk": 1.0,
    "require_stop": True, "no_entries_before": "09:30", "no_entries_after": "15:00",
    "flat_by": "15:15", "blocked": [], "paper": True,
}


class SizeError(Exception):
    pass


def ist_to_utc(local_naive):
    return local_naive - datetime.timedelta(hours=5, minutes=30)


def to_ist(utc_naive):
    return utc_naive + datetime.timedelta(hours=5, minutes=30)


def parse_now(text):
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise SizeError('--now wants a time like "2026-10-29 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def load_json(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        raise SizeError("no %s" % path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_config():
    path = os.path.join(DATA_DIR, "config.json")
    if not os.path.exists(path):
        raise SizeError('no %s -- copy config.example.json to trader_in/config.json and set budget_inr, '
                         "max_loss_per_trade_inr and the other limits first" % path)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(path))
    return cfg


def fresh_price(symbol, plan_entry, now_utc):
    """A price under 15 minutes old, if HIGHER than the plan's entry, raises the risk."""
    path = os.path.join(DATA_DIR, "prices.json")
    if not os.path.exists(path):
        return plan_entry, "the plan's entry (no trader_in/prices.json)"
    doc = load_json(path, {})
    detail = (doc.get("detail") or {}).get(symbol)
    price = (doc.get("prices") or {}).get(symbol)
    if price is None:
        return plan_entry, "the plan's entry (no fresh price for %s)" % symbol
    as_of = (detail or {}).get("as_of") or doc.get("as_of")
    try:
        age_min = (now_utc - datetime.datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 60.0
    except (ValueError, TypeError):
        age_min = 999
    if age_min > 15:
        return plan_entry, "the plan's entry (price is %.0f min old, too stale to use)" % age_min
    if price > plan_entry:
        return price, "a fresher, higher quote (₹%.2f, %.0f min old)" % (price, age_min)
    return plan_entry, "the plan's entry (the fresh quote ₹%.2f is not higher)" % price


def cmd_default(a):
    now_utc = parse_now(a.now)
    cfg = load_config()
    plan = load_json(os.path.join(DATA_DIR, "plan.json"))
    if plan.get("date") != to_ist(now_utc).date().isoformat():
        raise SizeError("the plan in trader_in/plan.json is from %s, not today; make a fresh one with trader-exit" % plan.get("date"))

    symbol = plan["symbol"]
    if symbol in cfg.get("blocked", []):
        raise SizeError("%s is on your blocked list" % symbol)

    stop, target = plan["stop"], plan["target"]
    if stop >= plan["entry"]:
        raise SizeError("the plan's stop is not below its entry; something is wrong with trader_in/plan.json")
    if target <= plan["entry"]:
        raise SizeError("the plan's target is not above its entry; the move may have already happened")

    if a.limit is True:
        entry = plan["entry"]
        entry_note = "the plan's entry (limit order)"
        order_type = "LIMIT"
    elif a.limit is not None:
        entry = a.limit
        entry_note = "your limit price"
        order_type = "LIMIT"
    else:
        entry, entry_note = fresh_price(symbol, plan["entry"], now_utc)
        order_type = "MARKET"

    risk_per_share = entry - stop
    if risk_per_share <= 0:
        raise SizeError("at ₹%.2f the stop is no longer below the entry; the setup has likely failed" % entry)
    reward_risk = (target - entry) / risk_per_share
    if reward_risk < cfg["min_reward_risk"]:
        raise SizeError("reward:risk %.2f is under your minimum %.2f at this entry; the move has mostly happened"
                         % (reward_risk, cfg["min_reward_risk"]))

    budget = cfg["budget_inr"]
    cash_buffer = budget * cfg["cash_buffer_pct"] / 100.0
    portfolio = load_json(os.path.join(DATA_DIR, "portfolio.json"), {"cash_inr": budget - cash_buffer})
    cash = portfolio.get("cash_inr", budget - cash_buffer)

    limits = {
        "max loss": int(cfg["max_loss_per_trade_inr"] / risk_per_share),
        "position cap": int(budget * cfg["max_position_pct"] / 100.0 / entry),
        "max order": int(cfg["max_order_inr"] / entry),
        "cash": min(int((budget - cash_buffer) / entry), int(cash / entry)),
    }
    qty = min(limits.values())
    decided_by = min(limits, key=lambda k: limits[k])
    if qty <= 0:
        raise SizeError("sizes to 0 shares (tightest limit: %s = %d shares); widen the stop, raise the budget, or skip this trade"
                         % (decided_by, limits[decided_by]))
    order_value = qty * entry
    if order_value < cfg["min_order_inr"]:
        raise SizeError("%d shares = ₹%.2f, under your minimum order ₹%.2f" % (qty, order_value, cfg["min_order_inr"]))

    max_loss = round(qty * risk_per_share, 2)
    why = ("opening-range breakout on %s: %d shares, bound by your %s limit (%d x ₹%.2f = ₹%.2f at the stop)"
           % (symbol, qty, decided_by, qty, risk_per_share, max_loss))

    order = {
        "symbol": symbol, "side": "buy", "qty": str(qty), "order_type": order_type,
        "product": "CNC", "validity": "DAY",
        "limit_price": ("%.2f" % entry) if order_type == "LIMIT" else None,
        "stop_loss_price": "%.2f" % stop, "take_profit_price": "%.2f" % target,
        "why": why,
        "plan": {"entry": entry, "entry_note": entry_note, "stop": stop, "target": target,
                 "risk_per_share": round(risk_per_share, 2), "reward_risk": round(reward_risk, 2),
                 "max_loss": max_loss, "order_value": round(order_value, 2), "decided_by": decided_by,
                 "setup": plan.get("entry_time", ""), "flat_by": plan.get("flat_by", cfg["flat_by"])},
    }

    if a.json:
        print(json.dumps(order, indent=2))
    else:
        print("Sizing %s: entry ₹%.2f (%s), stop ₹%.2f, target ₹%.2f. Risk ₹%.2f/share, reward:risk %.2f."
              % (symbol, entry, entry_note, stop, target, risk_per_share, reward_risk))
        for name, shares in limits.items():
            mark = "  <- decides it" if name == decided_by else ""
            print("  %-12s %d shares%s" % (name, shares, mark))
        print("%d shares = ₹%.2f. Max loss at the stop: %d x ₹%.2f = ₹%.2f (limit ₹%.2f)."
              % (qty, order_value, qty, risk_per_share, max_loss, cfg["max_loss_per_trade_inr"]))
        print("CNC (cash/delivery), no leverage. Nothing has been placed -- next is /trader-risk.")

    if a.write:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "orders.json"), "w", encoding="utf-8") as fh:
            json.dump({"orders": [order]}, fh, indent=2)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE position sizing (CNC, no leverage)")
    p.add_argument("--limit", nargs="?", const=True, type=lambda x: x, default=None,
                    help="a limit order: bare flag uses the plan's entry, or give a price")
    p.add_argument("--now")
    p.add_argument("--write", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if isinstance(a.limit, str):
        a.limit = float(a.limit)
    try:
        return cmd_default(a)
    except SizeError as e:
        print("Refused: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
