#!/usr/bin/env python3
"""Entry Timer for NSE/BSE setups. Standard library only.

    python3 entry.py check RELIANCE                  # 15-minute opening range, 5-minute bars
    python3 entry.py check RELIANCE --write           # also save trader_in/entry.json
    python3 entry.py check RELIANCE --setup level --level 1180.00
    python3 entry.py check RELIANCE --min-volume-x 1.5
    python3 entry.py watch RELIANCE --until 15:15     # one line per closed bar, stops on the trigger
    python3 entry.py watchlist                        # every hit in trader_in/watchlist.json

Other flags: --or-minutes N (default 15), --json, --offline FILE (a saved
Yahoo chart JSON), --now "2026-10-29 10:15" (IST) to replay a day. `watch`
with --now or --offline runs on a simulated clock and replays the day bar by
bar without waiting.

The rule: the opening range is the high and low of the first --or-minutes
of the NSE cash session (09:15 IST start). The trigger is the first
COMPLETED 5-minute bar after the range that CLOSES above the range high (or
above a --level you give it). A wick above is not a trigger; only a closed
bar counts. Long side only. It never places an order.
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; trader-india-skill/1.0)"
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
BACKOFF = 2.0
DATA_DIR = "trader_in"
EPOCH = datetime.datetime(1970, 1, 1)

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


class EntryError(Exception):
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
        raise EntryError('--now wants a time like "2026-10-29 10:15" (IST) or ...Z (UTC)')
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


def closed_bar_index(ts, now_utc, interval_minutes=5):
    now_s = (now_utc - EPOCH).total_seconds()
    n = 0
    for t in ts:
        if t is None or t + interval_minutes * 60 > now_s:
            break
        n += 1
    return n


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
        raise EntryError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def fetch_chart(sym, interval="5m", rng="1d", offline=None):
    if offline:
        opener = open
        path = offline
        if os.path.isdir(offline):
            path = os.path.join(offline, "%s.json" % bare_symbol(sym))
        with opener(path, encoding="utf-8") as fh:
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
    raise EntryError("%s answered HTTP %d" % (last[0].split("?")[0], last[1]))


def build_bars(data, now_utc):
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    ts_full = res.get("timestamp") or []
    n = closed_bar_index(ts_full, now_utc)
    return {"ts": ts_full[:n], "open": (q.get("open") or [])[:n], "high": (q.get("high") or [])[:n],
            "low": (q.get("low") or [])[:n], "close": (q.get("close") or [])[:n], "volume": (q.get("volume") or [])[:n]}


def check(sym, now_utc, or_minutes=15, setup="orb", level=None, min_volume_x=None, offline=None, interval="5m"):
    local = to_ist(now_utc)
    d = local.date()
    ok, reason = trading_day(d)
    if not ok:
        return {"status": "closed", "reason": reason}
    if local.time() < OPEN:
        return {"status": "not_open", "reason": "before 09:15 IST"}
    bar_minutes = 5 if interval == "5m" else 1
    n_range = max(1, or_minutes // bar_minutes)

    data = fetch_chart(sym, interval=interval, offline=offline)
    bars = build_bars(data, now_utc)
    n_bars = len(bars["ts"])
    if setup == "orb":
        if n_bars <= n_range:
            range_end = (datetime.datetime.combine(d, OPEN) + datetime.timedelta(minutes=or_minutes)).time()
            return {"status": "forming", "reason": "opening range still forming (closes %s IST)" % range_end.strftime("%H:%M")}
        range_high = max(bars["high"][:n_range])
        range_low = min(bars["low"][:n_range])
        start_idx = n_range
    else:  # level
        if level is None:
            raise EntryError("--setup level needs --level PRICE")
        range_high, range_low = level, None
        start_idx = 0

    avg_range_vol = (sum(bars["volume"][:n_range]) / n_range) if setup == "orb" and n_range else None

    for j in range(start_idx, n_bars):
        c = bars["close"][j]
        if c is None or c <= range_high:
            continue
        if min_volume_x and avg_range_vol:
            v = bars["volume"][j] or 0
            if v < min_volume_x * avg_range_vol:
                continue
        bar_close_utc = datetime.datetime.utcfromtimestamp(bars["ts"][j]) + datetime.timedelta(minutes=bar_minutes)
        trigger_time = to_ist(bar_close_utc)
        return {"status": "triggered", "symbol": bare_symbol(sym), "setup": setup, "bar": j,
                "price": round(c, 2), "level": round(range_high, 2),
                "time_ist": trigger_time.strftime("%I:%M %p").lstrip("0"),
                "over_pct": round((c / range_high - 1) * 100, 2),
                "range_high": round(range_high, 2) if setup == "orb" else None,
                "range_low": round(range_low, 2) if range_low is not None else None, "date": d.isoformat()}

    last_close = next((c for c in reversed(bars["close"]) if c is not None), None)
    if last_close is None:
        return {"status": "no_data", "reason": "no usable bars yet"}
    diff = (last_close / range_high - 1) * 100
    return {"status": "waiting", "last_close": round(last_close, 2), "level": round(range_high, 2),
            "diff_pct": round(diff, 2), "range_low": round(range_low, 2) if range_low is not None else None}


def cmd_check(a):
    now_utc = parse_now(a.now)
    try:
        r = check(a.symbol, now_utc, or_minutes=a.or_minutes, setup=a.setup, level=a.level,
                  min_volume_x=a.min_volume_x, offline=a.offline, interval=a.interval)
    except EntryError as e:
        print("Error: %s" % e)
        return 1
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(format_result(a.symbol, r))
    if r["status"] == "triggered" and a.write:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "entry.json"), "w", encoding="utf-8") as fh:
            json.dump(r, fh, indent=2)
    return 0 if r["status"] == "triggered" else 1


def format_result(symbol, r):
    sym = bare_symbol(symbol)
    if r["status"] in ("closed", "not_open", "forming", "no_data"):
        return "%s  %s" % (sym, r["reason"])
    if r["status"] == "waiting":
        sign = "under" if r["diff_pct"] < 0 else "over"
        extra = " (range low ₹%.2f)" % r["range_low"] if r.get("range_low") is not None else ""
        return ("WAITING  %s last close ₹%.2f, %.2f%% %s the level ₹%.2f%s."
                % (sym, r["last_close"], abs(r["diff_pct"]), sign, r["level"], extra))
    return ("TRIGGERED %s at %s on ₹%.2f: bar %d closed above ₹%.2f (+%.2f%%). Nothing was placed."
            % (sym, r["time_ist"], r["price"], r["bar"], r["level"], r["over_pct"]))


def cmd_watch(a):
    print("Watching %s until %s IST (Ctrl-C to stop)..." % (bare_symbol(a.symbol), a.until or "close"))
    while True:
        now_utc = parse_now(a.now) if a.now else datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
        local = to_ist(now_utc)
        try:
            r = check(a.symbol, now_utc, or_minutes=a.or_minutes, setup=a.setup, level=a.level,
                      min_volume_x=a.min_volume_x, offline=a.offline, interval=a.interval)
        except EntryError as e:
            print("Error: %s" % e)
            return 1
        print("[%s] %s" % (local.strftime("%H:%M"), format_result(a.symbol, r)))
        if r["status"] == "triggered":
            if a.write or True:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(os.path.join(DATA_DIR, "entry.json"), "w", encoding="utf-8") as fh:
                    json.dump(r, fh, indent=2)
            return 0
        if r["status"] in ("closed", "no_data"):
            return 1
        cutoff = a.until or "15:30"
        if local.strftime("%H:%M") >= cutoff:
            print("Expired: no trigger by %s IST." % cutoff)
            return 1
        if a.now or a.offline:
            # replay mode: step the simulated clock forward one bar instead of sleeping
            a.now = (local + datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
            continue
        time.sleep(60)


def cmd_watchlist(a):
    path = a.watchlist or os.path.join(DATA_DIR, "watchlist.json")
    if not os.path.exists(path):
        print("No %s; run trader-scanner first." % path)
        return 1
    with open(path, encoding="utf-8") as fh:
        wl = json.load(fh)
    now_utc = parse_now(a.now)
    any_trigger = False
    for h in wl.get("hits", []):
        try:
            r = check(h["symbol"], now_utc, or_minutes=a.or_minutes)
        except EntryError as e:
            print("%s  error: %s" % (h["symbol"], e))
            continue
        print(format_result(h["symbol"], r))
        any_trigger = any_trigger or r["status"] == "triggered"
    return 0 if any_trigger else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE entry timer")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--setup", choices=["orb", "level"], default="orb")
        sp.add_argument("--level", type=float)
        sp.add_argument("--or-minutes", type=int, default=15)
        sp.add_argument("--interval", choices=["5m", "1m"], default="5m")
        sp.add_argument("--min-volume-x", type=float)
        sp.add_argument("--offline")
        sp.add_argument("--now")

    pc = sub.add_parser("check")
    pc.add_argument("symbol")
    common(pc)
    pc.add_argument("--write", action="store_true")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=cmd_check)

    pw = sub.add_parser("watch")
    pw.add_argument("symbol")
    common(pw)
    pw.add_argument("--until")
    pw.add_argument("--write", action="store_true")
    pw.set_defaults(func=cmd_watch)

    pl = sub.add_parser("watchlist")
    pl.add_argument("--watchlist")
    pl.add_argument("--or-minutes", type=int, default=15)
    pl.add_argument("--now")
    pl.set_defaults(func=cmd_watchlist)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except EntryError as e:
        print("Error: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
