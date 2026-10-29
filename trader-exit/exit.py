#!/usr/bin/env python3
"""Exit Planner for NSE/BSE day trades. Standard library only.

    python3 exit.py plan                              # trader_in/entry.json: midpoint stop, 2R target
    python3 exit.py plan --stop or-mid --target 1R --write
    python3 exit.py plan --stop atr:1.5               # needs the session's bars (Yahoo or --offline)
    python3 exit.py plan --symbol RELIANCE --entry 1174.30 --or-high 1170.50 --or-low 1156.20
    python3 exit.py show                              # the current trader_in/plan.json

Other flags: --flat-by 15:15, --json, --now "2026-10-29 10:15" (IST), --entry
PRICE with a waiting entry.json to plan ahead, --offline FILE (a saved Yahoo
chart JSON for an ATR stop).

Stops round AWAY from the entry to the NSE tick size (Rs 0.05 for cash
equities); targets round to the tick toward the stop's side. Long side only.
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

UA = "Mozilla/5.0 (compatible; trader-india-skill/1.0)"
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
BACKOFF = 2.0
DATA_DIR = "trader_in"
TICK = Decimal("0.05")  # NSE cash-equity tick size

NSE_HOLIDAYS = {
    "2026-01-26": "Republic Day", "2026-02-19": "Chhatrapati Shivaji Maharaj Jayanti",
    "2026-03-31": "Id-Ul-Fitr (Eid)", "2026-04-02": "Ram Navami", "2026-04-10": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2026-05-01": "Maharashtra Day",
    "2026-06-27": "Id-Ul-Adha (Bakri Id)", "2026-08-15": "Independence Day",
    "2026-08-27": "Ganesh Chaturthi", "2026-10-02": "Gandhi Jayanti / Dussehra",
    "2026-10-20": "Diwali Laxmi Pujan (cash market closed; Muhurat trading only, evening)",
    "2026-10-21": "Diwali Balipratipada", "2026-11-05": "Guru Nanak Jayanti", "2026-12-25": "Christmas",
}
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
DEFAULT_FLAT_BY = "15:15"  # 15 minutes before the 15:30 close


class ExitError(Exception):
    pass


def to_ist(utc_naive):
    return utc_naive + datetime.timedelta(hours=5, minutes=30)


def ist_to_utc(local_naive):
    return local_naive - datetime.timedelta(hours=5, minutes=30)


def parse_now(text):
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ExitError('--now wants a time like "2026-10-29 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    return (False, "NSE/BSE holiday: " + name) if name else (True, "")


def yahoo_symbol(sym):
    sym = sym.upper().strip()
    return sym if sym.endswith(".NS") or sym.endswith(".BO") else sym + ".NS"


def bare_symbol(sym):
    sym = sym.upper().strip()
    for suf in (".NS", ".BO"):
        if sym.endswith(suf):
            return sym[: -len(suf)]
    return sym


def floor_tick(x):
    try:
        d = Decimal(str(x))
    except InvalidOperation:
        raise ExitError("bad price: %r" % x)
    return float((d / TICK).to_integral_value(rounding=ROUND_FLOOR) * TICK)


def ceil_to_tick_half_up(x):
    d = Decimal(str(x))
    return float((d / TICK).to_integral_value(rounding=ROUND_HALF_UP) * TICK)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except urllib.error.URLError as e:
        raise ExitError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def fetch_chart(sym, interval="5m", rng="1d", offline=None):
    if offline:
        with open(offline, encoding="utf-8") as fh:
            return json.load(fh)
    last = None
    for host in YAHOO_HOSTS:
        url = "https://%s/v8/finance/chart/%s?range=%s&interval=%s" % (host, yahoo_symbol(sym), rng, interval)
        code, text = _get(url)
        if code == 429:
            time.sleep(BACKOFF)
            code, text = _get(url)
        if code == 200:
            return json.loads(text)
        last = (url, code)
        if code != 429 and code < 500:
            break
    raise ExitError("%s answered HTTP %d" % (last[0].split("?")[0], last[1]))


def atr_stop(sym, entry_price, entry_bar, k, offline=None):
    data = fetch_chart(sym, offline=offline)
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    highs = q.get("high") or []
    lows = q.get("low") or []
    closes = q.get("close") or []
    n = min(entry_bar + 1, len(closes))
    if n < 2:
        raise ExitError("not enough bars before the entry for an ATR stop")
    trs = []
    prev_close = closes[0]
    for i in range(1, n):
        if highs[i] is None or lows[i] is None or closes[i] is None:
            continue
        tr = max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close))
        trs.append(tr)
        prev_close = closes[i]
    if not trs:
        raise ExitError("no usable bars for an ATR stop")
    window = trs[-14:]
    atr = sum(window) / len(window)
    note = "ATR14" if len(window) == 14 else "ATR%d (fewer than 14 bars before the entry)" % len(window)
    return entry_price - k * atr, note, atr


def plan_exit(symbol, entry, entry_time, or_high, or_low, stop_spec, target_spec, flat_by, date, entry_bar=None, offline=None):
    symbol = bare_symbol(symbol)
    stop_rule = ""
    if stop_spec == "or-mid":
        if or_high is None or or_low is None:
            raise ExitError("or-mid needs the opening range (--or-high/--or-low, or a level setup's entry.json)")
        raw = (or_high + or_low) / 2.0
        stop_rule = "opening-range midpoint: (%.2f + %.2f) / 2 = %.4f, rounded down to the tick" % (or_high, or_low, raw)
    elif stop_spec == "or-low":
        if or_low is None:
            raise ExitError("or-low needs the opening range")
        raw = or_low
        stop_rule = "opening-range low"
    elif stop_spec.startswith("price:"):
        raw = float(stop_spec.split(":", 1)[1])
        stop_rule = "your price"
    elif stop_spec.startswith("pct:"):
        pct = float(stop_spec.split(":", 1)[1])
        raw = entry * (1 - pct / 100.0)
        stop_rule = "%.2f%% under the entry" % pct
    elif stop_spec.startswith("atr:"):
        k = float(stop_spec.split(":", 1)[1])
        if entry_bar is None:
            raise ExitError("atr stop needs the entry bar index (from entry.json)")
        raw, note, atr_val = atr_stop(symbol, entry, entry_bar, k, offline=offline)
        stop_rule = "%s x %.2f (%s) under the entry" % (("%.1f" % k).rstrip("0").rstrip("."), atr_val, note)
    else:
        raise ExitError("unknown --stop %r" % stop_spec)

    stop = floor_tick(raw)
    if stop >= entry:
        raise ExitError("stop %.2f is at or above the entry %.2f; refused" % (stop, entry))
    risk = entry - stop
    if risk < entry * 0.0005:
        raise ExitError("risk %.4f is under 0.05%% of the price: too tight to survive the spread" % risk)

    if target_spec.endswith("R") and target_spec[:-1].replace(".", "", 1).isdigit():
        r_mult = float(target_spec[:-1])
        raw_t = entry + r_mult * risk
        target_rule = "%sR (%s times the risk)" % (target_spec[:-1], target_spec[:-1])
    elif target_spec.startswith("price:"):
        raw_t = float(target_spec.split(":", 1)[1])
        r_mult = (raw_t - entry) / risk
        target_rule = "your price"
    else:
        raise ExitError("unknown --target %r" % target_spec)

    target = ceil_to_tick_half_up(raw_t)
    if target <= entry:
        raise ExitError("target %.2f is at or below the entry %.2f; refused" % (target, entry))

    return {
        "symbol": symbol, "date": date, "entry": round(entry, 2), "entry_time": entry_time,
        "entry_bar": entry_bar, "opening_range": {"high": or_high, "low": or_low} if or_high else None,
        "stop": stop, "stop_rule": stop_rule, "target": target, "target_r": round(r_mult, 2),
        "risk_per_share": round(risk, 2), "reward_risk": round((target - entry) / risk, 2),
        "flat_by": flat_by, "tick_size": float(TICK),
    }


def cmd_plan(a):
    now_utc = parse_now(a.now)
    local = to_ist(now_utc)
    date = local.date().isoformat()

    if a.symbol and a.entry is not None:
        symbol, entry = a.symbol, a.entry
        or_high, or_low = a.or_high, a.or_low
        entry_time, entry_bar = "(given)", None
    else:
        path = os.path.join(DATA_DIR, "entry.json")
        if not os.path.exists(path):
            raise ExitError("no %s and no --symbol/--entry given; run trader-entry first" % path)
        with open(path, encoding="utf-8") as fh:
            e = json.load(fh)
        if e.get("status") != "triggered":
            raise ExitError("trader_in/entry.json has not triggered (status: %s)" % e.get("status"))
        if e.get("date") != date:
            raise ExitError("trader_in/entry.json is from %s, not today (%s); re-run trader-entry" % (e.get("date"), date))
        symbol = e["symbol"]
        entry = e["price"]
        or_high = a.or_high if a.or_high is not None else e.get("range_high")
        or_low = a.or_low if a.or_low is not None else e.get("range_low")
        entry_time = e.get("time_ist")
        entry_bar = e.get("bar")

    flat_by = a.flat_by or DEFAULT_FLAT_BY
    ok, reason = trading_day(local.date())
    if ok and local.strftime("%H:%M") >= flat_by:
        raise ExitError("it is %s IST, at or after flat-by %s; too late to plan a new entry today" % (local.strftime("%H:%M"), flat_by))

    plan = plan_exit(symbol, entry, entry_time, or_high, or_low, a.stop, a.target, flat_by, date, entry_bar, offline=a.offline)

    if a.json:
        print(json.dumps(plan, indent=2))
    else:
        print("Exit plan: %s long, %s" % (plan["symbol"], plan["date"]))
        print("  Entry         ₹%.2f at %s" % (plan["entry"], plan["entry_time"]))
        print("  Stop          ₹%.2f  %s" % (plan["stop"], plan["stop_rule"]))
        print("  Target        ₹%.2f  %s" % (plan["target"], "%sR (%s times the risk)" % (plan["target_r"], plan["target_r"])))
        print("  Risk a share  ₹%.2f (%.2f%% of the entry)" % (plan["risk_per_share"], plan["risk_per_share"] / plan["entry"] * 100))
        print("  Reward:risk   %.2f" % plan["reward_risk"])
        print("  Flat by       %s IST, win or lose: no overnight hold" % plan["flat_by"])
    if a.write:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "plan.json"), "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2)
    return 0


def cmd_show(a):
    path = os.path.join(DATA_DIR, "plan.json")
    if not os.path.exists(path):
        print("No trader_in/plan.json yet; run: exit.py plan --write")
        return 1
    with open(path, encoding="utf-8") as fh:
        plan = json.load(fh)
    print(json.dumps(plan, indent=2) if a.json else
          "%s long %s: entry ₹%.2f, stop ₹%.2f, target ₹%.2f, R:R %.2f, flat by %s"
          % (plan["symbol"], plan["date"], plan["entry"], plan["stop"], plan["target"], plan["reward_risk"], plan["flat_by"]))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE exit planner")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("plan")
    pp.add_argument("--stop", default="or-mid")
    pp.add_argument("--target", default="2R")
    pp.add_argument("--flat-by")
    pp.add_argument("--symbol")
    pp.add_argument("--entry", type=float)
    pp.add_argument("--or-high", type=float)
    pp.add_argument("--or-low", type=float)
    pp.add_argument("--offline")
    pp.add_argument("--now")
    pp.add_argument("--write", action="store_true")
    pp.add_argument("--json", action="store_true")
    pp.set_defaults(func=cmd_plan)

    ps = sub.add_parser("show")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_show)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except ExitError as e:
        print("Refused: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
