#!/usr/bin/env python3
"""Risk Manager for NSE/BSE orders. Standard library only.

    python3 check.py                           # trader_in/ files; writes trader_in/state/risk.json
    python3 check.py --json
    python3 check.py --no-write                # look without recording
    python3 check.py --now "2026-10-29 10:15"  # pin the clock (IST)

Entries (a buy, or a sell that would go short -- shorting is never allowed
in this pack) are checked against every limit below and BLOCKED on a
failure. Exits (a sell that only reduces a held position) are checked for
format and asset only; every other check becomes a warning, never a BLOCK,
so getting out is always possible.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

DATA_DIR = "trader_in"
UA = "Mozilla/5.0 (compatible; trader-india-skill/1.0)"
NSE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

NSE_HOLIDAYS = {
    "2026-01-26": "Republic Day", "2026-02-19": "Chhatrapati Shivaji Maharaj Jayanti",
    "2026-03-31": "Id-Ul-Fitr (Eid)", "2026-04-02": "Ram Navami", "2026-04-10": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2026-05-01": "Maharashtra Day",
    "2026-06-27": "Id-Ul-Adha (Bakri Id)", "2026-08-15": "Independence Day",
    "2026-08-27": "Ganesh Chaturthi", "2026-10-02": "Gandhi Jayanti / Dussehra",
    "2026-10-20": "Diwali Laxmi Pujan (cash market closed; Muhurat trading only, evening)",
    "2026-10-21": "Diwali Balipratipada", "2026-11-05": "Guru Nanak Jayanti", "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
TICKER_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")

DEFAULT_CONFIG = {
    "budget_inr": 100000, "cash_buffer_pct": 10, "max_loss_per_trade_inr": 1000,
    "max_position_pct": 75, "max_order_inr": 80000, "min_order_inr": 500,
    "max_daily_loss_inr": 3000, "max_trades_per_day": 3, "min_reward_risk": 1.0,
    "require_stop": True, "no_entries_before": "09:30", "no_entries_after": "15:00",
    "flat_by": "15:15", "blocked": [], "paper": True, "max_orders_per_day": 10,
}


def to_ist(utc_naive):
    return utc_naive + datetime.timedelta(hours=5, minutes=30)


def ist_to_utc(local_naive):
    return local_naive - datetime.timedelta(hours=5, minutes=30)


def parse_now(text):
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(t)
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    return (False, "NSE/BSE holiday: " + name) if name else (True, "")


def market_status(now_utc):
    local = to_ist(now_utc)
    d = local.date()
    ok, reason = trading_day(d)
    st = {"now_ist": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(), "open": False, "reason": reason}
    if ok:
        if OPEN <= local.time() < CLOSE:
            st["open"] = True
            st["reason"] = "regular session"
        elif local.time() < OPEN:
            st["reason"] = "before the open"
        else:
            st["reason"] = "after the close"
    return st


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return default


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(os.path.join(DATA_DIR, "config.json"), {}))
    return cfg


def load_universe():
    path = os.path.join(DATA_DIR, "universe.txt")
    if not os.path.exists(path):
        return None
    out = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            sym = line.split("|", 1)[0].strip().upper()
            if sym:
                out.add(sym)
    return out


def fetch_circuit_band(symbol, timeout=10):
    """(lower, upper) price band, or None if NSE's quote API is unreachable.
    This endpoint (unlike corporate-announcements) blocked plain scripted
    requests in testing -- it fails soft and the caller must treat None as
    "unknown", never as "no band"."""
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    headers = {"User-Agent": NSE_UA, "Accept": "text/html"}
    try:
        opener.open(urllib.request.Request("https://www.nseindia.com/", headers=headers), timeout=timeout).read()
        req = urllib.request.Request("https://www.nseindia.com/api/quote-equity?symbol=%s" % symbol,
                                      headers={**headers, "Accept": "application/json"})
        with opener.open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        pi = data.get("priceInfo", {}).get("pPriceBand") or {}
        lower, upper = pi.get("lowerPrice"), pi.get("upperPrice")
        if lower is None or upper is None:
            return None
        return float(lower), float(upper)
    except Exception:
        return None


def fresh_price(symbol, now_utc):
    doc = load_json(os.path.join(DATA_DIR, "prices.json"), {})
    price = (doc.get("prices") or {}).get(symbol)
    detail = (doc.get("detail") or {}).get(symbol, {})
    as_of = detail.get("as_of") or doc.get("as_of")
    if price is None or not as_of:
        return None, None
    try:
        age_min = (now_utc - datetime.datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 60.0
    except ValueError:
        return None, None
    return (price, age_min) if age_min <= 15 else (None, age_min)


def fingerprint(order):
    key = json.dumps(order, sort_keys=True)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def check_order(order, idx, cfg, now_utc, universe, portfolio, broker_state, today_entries_risk, circuit_cache):
    issues, warnings = [], []
    symbol = (order.get("symbol") or "").upper()
    side = (order.get("side") or "").lower()
    qty_raw = order.get("qty")
    product = order.get("product", "")

    if not symbol or side not in ("buy", "sell") or not qty_raw:
        return {"verdict": "BLOCK", "issues": ["format: symbol, a buy/sell side and qty are all required"], "warnings": []}
    try:
        qty = int(qty_raw)
        if qty <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return {"verdict": "BLOCK", "issues": ["format: qty must be a positive whole number of shares"], "warnings": []}
    if product and product != "CNC":
        return {"verdict": "BLOCK", "issues": ["asset: product %r is not supported; this pack sizes CNC (cash/delivery) only" % product], "warnings": []}
    if not TICKER_RE.match(symbol):
        return {"verdict": "BLOCK", "issues": ["format: %r is not a plain NSE ticker" % symbol], "warnings": []}

    held = (portfolio.get("positions") or {}).get(symbol, 0)
    is_exit = side == "sell" and qty <= held

    if universe is not None and symbol not in universe:
        issues.append("asset: %s is not in trader_in/universe.txt (run trader-scanner universe, or it may be delisted/renamed)" % symbol)
    if side == "sell" and qty > held:
        issues.append("short: selling %d but only %d held; this pack never allows short sales" % (qty, held))

    if is_exit:
        verdict = "BLOCK" if issues else "PASS"
        return {"verdict": verdict, "issues": issues, "warnings": warnings, "is_exit": True, "qty": qty, "symbol": symbol}

    # ---- entry-only checks below ----
    if symbol in cfg.get("blocked", []):
        issues.append("blocked: %s is on your blocked list" % symbol)

    plan = (order.get("plan") or {})
    stop = order.get("stop_loss_price")
    target = order.get("take_profit_price")
    stop = float(stop) if stop else plan.get("stop")
    target = float(target) if target else plan.get("target")
    plan_entry = plan.get("entry")
    price, price_age = fresh_price(symbol, now_utc)
    entry = plan_entry
    entry_note = "the plan's entry"
    if order.get("order_type") == "LIMIT" and order.get("limit_price"):
        entry = float(order["limit_price"])
        entry_note = "the limit price"
    elif price is not None and plan_entry is not None and price > plan_entry:
        entry = price
        entry_note = "a fresher, higher quote (%.0f min old)" % price_age

    if cfg.get("require_stop", True) and not stop:
        issues.append("stop: no stop given and require_stop is true")
    elif stop and entry and stop >= entry:
        issues.append("stop: stop ₹%.2f is not below the entry ₹%.2f" % (stop, entry))

    order_value = qty * (entry or 0)
    if order_value and order_value < cfg["min_order_inr"]:
        issues.append("size: ₹%.2f is under your minimum order ₹%.2f" % (order_value, cfg["min_order_inr"]))
    if order_value and order_value > cfg["max_order_inr"]:
        issues.append("size: ₹%.2f is over your maximum order ₹%.2f" % (order_value, cfg["max_order_inr"]))
    budget = cfg["budget_inr"]
    if order_value and order_value > budget * cfg["max_position_pct"] / 100.0:
        issues.append("position: ₹%.2f is over %.0f%% of your ₹%.2f budget" % (order_value, cfg["max_position_pct"], budget))
    cash = portfolio.get("cash_inr", budget * (1 - cfg["cash_buffer_pct"] / 100.0))
    if order_value and order_value > cash:
        issues.append("cash: ₹%.2f needs more than the ₹%.2f cash on hand (no margin in this pack)" % (order_value, cash))

    max_loss = None
    if stop and entry:
        max_loss = round(qty * (entry - stop), 2)
        if max_loss > cfg["max_loss_per_trade_inr"]:
            issues.append("loss: %d shares x ₹%.2f risk a share = ₹%.2f at the stop, over your ₹%.2f max loss per trade"
                          % (qty, entry - stop, max_loss, cfg["max_loss_per_trade_inr"]))
    reward_risk = None
    if stop and target and entry and entry > stop:
        reward_risk = round((target - entry) / (entry - stop), 2)
        if reward_risk < cfg["min_reward_risk"]:
            issues.append("reward:risk: %.2f is under your minimum %.2f" % (reward_risk, cfg["min_reward_risk"]))

    today_risk_so_far = sum(today_entries_risk)
    if max_loss is not None and today_risk_so_far + max_loss > cfg["max_daily_loss_inr"]:
        issues.append("daily loss: today's entries would total ₹%.2f, over your ₹%.2f daily loss limit"
                      % (today_risk_so_far + max_loss, cfg["max_daily_loss_inr"]))
    n_today = len(broker_state.get("entries_today", []))
    if n_today >= cfg["max_trades_per_day"]:
        issues.append("trades: %d of %d trades already placed today" % (n_today, cfg["max_trades_per_day"]))
    n_orders_today = broker_state.get("orders_today", 0)
    if n_orders_today >= cfg.get("max_orders_per_day", 10):
        issues.append("daily limit: %d of %d orders already placed today" % (n_orders_today, cfg.get("max_orders_per_day", 10)))

    st = market_status(now_utc)
    local_hm = to_ist(now_utc).strftime("%H:%M")
    if not st["open"]:
        issues.append("window: market is not open (%s)" % st["reason"])
    elif local_hm < cfg["no_entries_before"] or local_hm > cfg["no_entries_after"]:
        issues.append("window: outside your entry window (%s-%s IST)" % (cfg["no_entries_before"], cfg["no_entries_after"]))

    seen_today = broker_state.get("entries_today", [])
    if any(e.get("symbol") == symbol and e.get("side") == side for e in seen_today):
        issues.append("duplicate: %s %s already placed today" % (side, symbol))

    if symbol not in circuit_cache:
        circuit_cache[symbol] = fetch_circuit_band(symbol)
    band = circuit_cache[symbol]
    if band is None:
        warnings.append("circuit-limit check skipped: NSE's quote API did not answer (it blocks a lot of scripted "
                         "access); verify yourself that the stop is within today's price band before trusting it to fill")
    elif stop is not None and not (band[0] <= stop <= band[1]):
        issues.append("circuit: stop ₹%.2f is outside today's price band ₹%.2f-₹%.2f; it cannot realistically fill" % (stop, band[0], band[1]))

    if cfg.get("paper") is False:
        warnings.append("this config is LIVE (paper: false)")

    verdict = "BLOCK" if issues else "PASS"
    return {"verdict": verdict, "issues": issues, "warnings": warnings, "is_exit": False, "qty": qty, "symbol": symbol,
            "entry": entry, "entry_note": entry_note, "stop": stop, "target": target, "max_loss": max_loss,
            "reward_risk": reward_risk, "order_value": round(order_value, 2) if order_value else None}


def cmd_check(a):
    now_utc = parse_now(a.now)
    cfg = load_config()
    doc = load_json(os.path.join(DATA_DIR, "orders.json"), {"orders": []})
    orders = doc.get("orders", [])
    universe = load_universe()
    portfolio = load_json(os.path.join(DATA_DIR, "portfolio.json"), {"cash_inr": cfg["budget_inr"] * (1 - cfg["cash_buffer_pct"] / 100.0), "positions": {}})
    broker_state = load_json(os.path.join(DATA_DIR, "state", "broker.json"), {})
    today = to_ist(now_utc).date().isoformat()
    today_entries = [e for e in broker_state.get("entries", []) if e.get("date") == today]
    today_risk = [e.get("max_loss", 0) for e in today_entries]
    bstate_today = {"entries_today": today_entries, "orders_today": sum(1 for o in broker_state.get("log", []) if o.get("date") == today)}

    results, circuit_cache = [], {}
    for i, order in enumerate(orders):
        r = check_order(order, i, cfg, now_utc, universe, portfolio, bstate_today, today_risk, circuit_cache)
        if r["verdict"] == "PASS" and not r.get("is_exit") and r.get("max_loss") is not None:
            today_risk.append(r["max_loss"])  # so a batch that is fine order by order but breaks a limit together is caught
        r["fingerprint"] = fingerprint(order)
        results.append(r)

    if not a.no_write:
        os.makedirs(os.path.join(DATA_DIR, "state"), exist_ok=True)
        verdicts = load_json(os.path.join(DATA_DIR, "state", "risk.json"), {"verdicts": {}})
        for r in results:
            verdicts["verdicts"][r["fingerprint"]] = {"date": today, "verdict": r["verdict"], "symbol": r["symbol"]}
        with open(os.path.join(DATA_DIR, "state", "risk.json"), "w", encoding="utf-8") as fh:
            json.dump(verdicts, fh, indent=2)

    passed = sum(1 for r in results if r["verdict"] == "PASS")
    blocked = len(results) - passed
    if a.json:
        print(json.dumps({"now_ist": to_ist(now_utc).strftime("%Y-%m-%d %H:%M"), "results": results}, indent=2))
    else:
        for i, r in enumerate(results, 1):
            kind = "exit" if r.get("is_exit") else "entry"
            print("%s #%d %s %s %s, %s" % (r["verdict"], i, orders[i - 1].get("side"), r.get("qty"), r.get("symbol"), kind))
            if not r.get("is_exit") and r.get("entry") is not None:
                print("          entry ₹%.2f (%s), stop ₹%s, target ₹%s"
                      % (r["entry"], r.get("entry_note", ""), r.get("stop"), r.get("target")))
                if r.get("max_loss") is not None:
                    print("          max loss ₹%.2f, reward:risk %s" % (r["max_loss"], r.get("reward_risk")))
            for w in r["warnings"]:
                print("        ! %s" % w)
            for iss in r["issues"]:
                print("        - %s" % iss)
        print("\n%d passed, %d blocked at %s IST." % (passed, blocked, to_ist(now_utc).strftime("%Y-%m-%d %H:%M")))
    return 0 if blocked == 0 else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE risk manager")
    p.add_argument("--now")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    return cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
