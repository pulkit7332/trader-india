#!/usr/bin/env python3
"""Time Machine for NSE/BSE day trades. Standard library only.

    python3 timemachine.py run                    # trader_in/plan.json, 10,000 replays, seed 19
    python3 timemachine.py run --write             # also save trader_in/timemachine.json
    python3 timemachine.py run --until 15:15       # end each replay at your flat-by time
    python3 timemachine.py run --json
    python3 timemachine.py run --symbol RELIANCE --date 2026-10-29 \\
        --entry 1174.30 --stop 1163.35 --target 1196.20 --entry-time "9:40 AM"

Other flags: --replays N, --seed N (default 19), --window N (default 2 bars),
--offline FILE (a saved Yahoo chart JSON), --now "2026-10-29 10:15" (IST).

How it works: 60 days of 5-minute bars (query2.finance.yahoo.com,
range=60d&interval=5m) give full NSE sessions of 75 bars each (09:15-15:30
IST). Every bar becomes four ratios to the bar before it (open, high, low,
close over the previous close). One replay starts at the entry price and,
for each remaining bar, draws a past bar at the same time of day (plus or
minus --window bars) at random, scales it to the current price, and checks
the stop then the target. 10,000 replays give the odds; the plan's own rule
also re-runs on every real past session (the history). Simulated odds, not
predictions -- say the seed so any run can be repeated.
"""

import argparse
import datetime
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; trader-india-skill/1.0)"
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
BACKOFF = 2.0
DATA_DIR = "trader_in"
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
BARS_PER_SESSION = 75  # (15:30 - 09:15) / 5 minutes, the full 5-minute grid
# Yahoo's 60d/5m history for NSE tickers consistently has no usable OHLC for
# the last two grid slots of every session (15:20-15:30): the closing-auction
# print lands in its own non-OHLC snapshot instead of a real 5-minute bar.
# The Monte Carlo pool and the history replay both work only up to this cutoff.
USABLE_BARS = 73

NSE_HOLIDAYS = {
    "2026-01-26", "2026-02-19", "2026-03-31", "2026-04-02", "2026-04-10", "2026-04-14",
    "2026-05-01", "2026-06-27", "2026-08-15", "2026-08-27", "2026-10-02", "2026-10-20",
    "2026-10-21", "2026-11-05", "2026-12-25",
}


class TMError(Exception):
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
        raise TMError('--now wants a time like "2026-10-29 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def yahoo_symbol(sym):
    sym = sym.upper().strip()
    return sym if sym.endswith(".NS") or sym.endswith(".BO") else sym + ".NS"


def bare_symbol(sym):
    sym = sym.upper().strip()
    for suf in (".NS", ".BO"):
        if sym.endswith(suf):
            return sym[: -len(suf)]
    return sym


def _get(url, timeout=25):
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
        raise TMError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def fetch_60d(sym, offline=None):
    if offline:
        with open(offline, encoding="utf-8") as fh:
            return json.load(fh)
    last = None
    for host in YAHOO_HOSTS:
        url = "https://%s/v8/finance/chart/%s?range=60d&interval=5m" % (host, yahoo_symbol(sym))
        code, text = _get(url)
        if code == 429:
            time.sleep(BACKOFF)
            code, text = _get(url)
        if code == 200:
            return json.loads(text)
        last = (url, code)
        if code != 429 and code < 500:
            break
    raise TMError("%s answered HTTP %d" % (last[0].split("?")[0], last[1]))


def split_sessions(data):
    """-> list of sessions, each a list of bar dicts {o,h,l,c,t}, oldest first.
    Only FULL sessions (BARS_PER_SESSION bars, no None) are kept."""
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    ts = res.get("timestamp") or []
    opens, highs, lows, closes = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or []
    by_date = {}
    for i, t in enumerate(ts):
        if t is None:
            continue
        d = to_ist(datetime.datetime.utcfromtimestamp(t)).date()
        by_date.setdefault(d, []).append(i)
    sessions = []
    for d in sorted(by_date):
        idxs = by_date[d]
        if len(idxs) != BARS_PER_SESSION:
            continue
        usable_idxs = idxs[:USABLE_BARS]
        bars = []
        ok = True
        for i in usable_idxs:
            if None in (opens[i], highs[i], lows[i], closes[i]):
                ok = False
                break
            bars.append({"o": opens[i], "h": highs[i], "l": lows[i], "c": closes[i], "t": ts[i]})
        if ok:
            sessions.append({"date": d.isoformat(), "bars": bars})
    return sessions


def bar_ratios(sessions):
    """Flatten to one continuous bar series and return per-session ratio lists
    (each bar's o/h/l/c over the close of the immediately preceding bar)."""
    flat = []
    for s in sessions:
        flat.extend(s["bars"])
    ratios_by_session = []
    prev_close = None
    idx = 0
    for s in sessions:
        rs = []
        for bar in s["bars"]:
            if prev_close is None:
                prev_close = bar["c"]
                rs.append({"o": 1.0, "h": bar["h"] / bar["o"], "l": bar["l"] / bar["o"], "c": 1.0})
            else:
                rs.append({"o": bar["o"] / prev_close, "h": bar["h"] / prev_close,
                           "l": bar["l"] / prev_close, "c": bar["c"] / prev_close})
            prev_close = bar["c"]
            idx += 1
        ratios_by_session.append(rs)
    return ratios_by_session


def monte_carlo(ratios_by_session, entry_bar, entry, stop, target, until_bar, replays, seed, window):
    rng = random.Random(seed)
    n_sessions = len(ratios_by_session)
    start_bar = entry_bar + 1
    bar_pool = {}
    for p in range(start_bar, USABLE_BARS):
        pool = []
        for si, rs in enumerate(ratios_by_session):
            for off in range(-window, window + 1):
                j = p + off
                if 0 <= j < len(rs):
                    pool.append(rs[j])
        bar_pool[p] = pool

    results = []  # each: (outcome, r, close_price)
    for _ in range(replays):
        price = entry
        outcome = None
        for p in range(start_bar, min(until_bar, USABLE_BARS)):
            pool = bar_pool.get(p)
            if not pool:
                continue
            ratio = rng.choice(pool)
            bar_high = price * ratio["h"]
            bar_low = price * ratio["l"]
            hit_stop = bar_low <= stop
            hit_target = bar_high >= target
            if hit_stop and hit_target:
                outcome = "stop"  # a bar touching both counts as the stop
            elif hit_stop:
                outcome = "stop"
            elif hit_target:
                outcome = "target"
            if outcome:
                break
            price = price * ratio["c"]
        if outcome is None:
            outcome = "neither"
        risk = entry - stop
        if outcome == "target":
            r = (target - entry) / risk
        elif outcome == "stop":
            r = -1.0
        else:
            r = (price - entry) / risk
        results.append((outcome, r, price))
    return results


def history_replay(sessions, or_minutes, stop_spec, target_r):
    """Re-run a comparable opening-range rule on every real past session."""
    n_range = max(1, or_minutes // 5)
    fired, target_first, stop_first, neither = 0, 0, 0, 0
    r_values = []
    for s in sessions:
        bars = s["bars"]
        if len(bars) <= n_range:
            continue
        range_high = max(b["h"] for b in bars[:n_range])
        range_low = min(b["l"] for b in bars[:n_range])
        entry_idx, entry_price = None, None
        for i in range(n_range, len(bars)):
            if bars[i]["c"] > range_high:
                entry_idx, entry_price = i, bars[i]["c"]
                break
        if entry_idx is None:
            continue
        fired += 1
        if stop_spec == "or-low" or stop_spec.startswith("atr") or stop_spec.startswith("price"):
            stop = range_low
        else:
            stop = (range_high + range_low) / 2.0
        if stop >= entry_price:
            neither += 1
            r_values.append(0.0)
            continue
        risk = entry_price - stop
        target = entry_price + target_r * risk
        outcome = "neither"
        for j in range(entry_idx + 1, len(bars)):
            if bars[j]["l"] <= stop and bars[j]["h"] >= target:
                outcome = "stop"
                break
            if bars[j]["l"] <= stop:
                outcome = "stop"
                break
            if bars[j]["h"] >= target:
                outcome = "target"
                break
        if outcome == "target":
            target_first += 1
            r_values.append(target_r)
        elif outcome == "stop":
            stop_first += 1
            r_values.append(-1.0)
        else:
            neither += 1
            last_close = bars[-1]["c"]
            r_values.append((last_close - entry_price) / risk)
    return {"sessions": len(sessions), "fired": fired, "target_first": target_first,
            "stop_first": stop_first, "neither": neither,
            "avg_r": round(statistics.fmean(r_values), 3) if r_values else None}


def verdict(mc_odds, target_r):
    hit_side = mc_odds["target_pct"] + mc_odds["stop_pct"]
    need = 100.0 / (1.0 + target_r)  # break-even win rate among replays that hit a side
    if hit_side == 0:
        return "No replay hit either side; nothing to judge.", None
    got = mc_odds["target_pct"] / hit_side * 100.0
    diff = got - need
    if abs(diff) < 5:
        label = "Close to a coin flip" if abs(target_r - 1.0) < 1e-9 else "Close to break-even"
        lean = label
    elif diff > 0:
        lean = ("Leans toward the target" if diff < 15 else "Strongly toward the target")
    else:
        lean = ("Leans toward the stop" if diff > -15 else "Strongly toward the stop")
    line = "%s: target first %.1f%% vs stop first %.1f%%." % (lean, mc_odds["target_pct"], mc_odds["stop_pct"])
    extra = None
    if mc_odds["neither_pct"] > 40:
        extra = "%.1f%% of the replays hit neither by the cutoff; the price then decides those." % mc_odds["neither_pct"]
    return line, extra


def cmd_run(a):
    now_utc = parse_now(a.now)
    if a.symbol and a.entry is not None:
        symbol = bare_symbol(a.symbol)
        entry, stop, target = a.entry, a.stop, a.target
        date = a.date or to_ist(now_utc).date().isoformat()
        entry_time = a.entry_time or ""
        or_minutes = a.or_minutes
        entry_bar = a.entry_bar
    else:
        path = os.path.join(DATA_DIR, "plan.json")
        if not os.path.exists(path):
            raise TMError("no %s and no --symbol/--entry/--stop/--target given; run trader-exit first" % path)
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
        symbol = plan["symbol"]
        entry, stop, target = plan["entry"], plan["stop"], plan["target"]
        date = plan["date"]
        entry_time = plan.get("entry_time", "")
        entry_bar = plan.get("entry_bar")
        or_minutes = 15

    if entry_bar is None:
        raise TMError("no entry bar index available; pass --entry-bar, or plan via trader-entry + trader-exit")
    if stop >= entry or target <= entry:
        raise TMError("plan makes no sense (stop %.2f, entry %.2f, target %.2f)" % (stop, entry, target))
    if entry_bar >= USABLE_BARS - 1:
        raise TMError("entry at bar %d is inside the last 15:20-15:30 IST window, where Yahoo's 5-minute "
                       "history has no usable OHLC; the replay needs at least one bar after the entry" % entry_bar)

    data = fetch_60d(symbol, offline=a.offline)
    sessions = split_sessions(data)
    past = [s for s in sessions if s["date"] < date]
    if len(past) < 20:
        raise TMError("only %d full past sessions available; need at least 20 for a fair pool" % len(past))

    until_time = a.until
    if until_time:
        hh, mm = (int(x) for x in until_time.split(":"))
        until_bar = ((hh * 60 + mm) - (OPEN.hour * 60 + OPEN.minute)) // 5
    else:
        until_bar = USABLE_BARS

    ratios = bar_ratios(past)
    target_r = round((target - entry) / (entry - stop), 4)
    results = monte_carlo(ratios, entry_bar, entry, stop, target, until_bar, a.replays, a.seed, a.window)

    n = len(results)
    n_target = sum(1 for o, _, _ in results if o == "target")
    n_stop = sum(1 for o, _, _ in results if o == "stop")
    n_neither = sum(1 for o, _, _ in results if o == "neither")
    closes = sorted(p for _, _, p in results)
    n_green = sum(1 for o, r, p in results if o == "target" or (o == "neither" and p >= entry))
    avg_r = statistics.fmean(r for _, r, _ in results)

    def pct(k):
        return round(k / n * 100.0, 1)

    mc = {"replays": n, "seed": a.seed, "window": a.window, "target_pct": pct(n_target), "stop_pct": pct(n_stop),
          "neither_pct": pct(n_neither), "green_pct": pct(n_green),
          "median_close": round(closes[n // 2], 2), "p5_close": round(closes[int(n * 0.05)], 2),
          "p95_close": round(closes[int(n * 0.95)], 2), "avg_r": round(avg_r, 3)}
    stop_spec_guess = "or-low" if abs(stop - min(b["l"] for b in past[-1]["bars"][:max(1, or_minutes // 5)])) < 1e-6 else "or-mid"
    hist = history_replay(past, or_minutes, stop_spec_guess, target_r)
    line, extra = verdict(mc, target_r)

    doc = {"plan": {"symbol": bare_symbol(symbol), "date": date, "entry": entry, "stop": stop, "target": target,
                     "entry_time": entry_time, "target_r": target_r},
           "mc": mc, "history": hist, "verdict": line, "verdict_extra": extra}

    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        print("%d replays of the rest of the session (seed %d, window ±%d bars)." % (n, a.seed, a.window))
        print("  target first   %.1f %%" % mc["target_pct"])
        print("  stop first     %.1f %%" % mc["stop_pct"])
        print("  neither        %.1f %%   still open at the cutoff" % mc["neither_pct"])
        print("  green          %.1f %%   target first, or above the entry at the cutoff" % mc["green_pct"])
        print("  Price at the cutoff: median ₹%.2f, 90%% of replays between ₹%.2f and ₹%.2f."
              % (mc["median_close"], mc["p5_close"], mc["p95_close"]))
        print("  Average R: %+.3f" % mc["avg_r"])
        print("History: the same opening-range rule on %d real past sessions fired %d times: %d target, %d stop, %d neither. Average R %s."
              % (hist["sessions"], hist["fired"], hist["target_first"], hist["stop_first"], hist["neither"],
                 ("%+.3f" % hist["avg_r"]) if hist["avg_r"] is not None else "n/a"))
        print(line)
        if extra:
            print(extra)
        print("Simulated from the last %d trading days; not a prediction. Earnings, news, circuit limits and halts can do things those days never did." % len(past))
    if a.write:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "timemachine.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE time machine")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run")
    pr.add_argument("--replays", type=int, default=10000)
    pr.add_argument("--seed", type=int, default=19)
    pr.add_argument("--window", type=int, default=2)
    pr.add_argument("--until")
    pr.add_argument("--or-minutes", type=int, default=15)
    pr.add_argument("--symbol")
    pr.add_argument("--date")
    pr.add_argument("--entry", type=float)
    pr.add_argument("--stop", type=float)
    pr.add_argument("--target", type=float)
    pr.add_argument("--entry-time")
    pr.add_argument("--entry-bar", type=int)
    pr.add_argument("--offline")
    pr.add_argument("--now")
    pr.add_argument("--write", action="store_true")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=cmd_run)
    a = p.parse_args(argv)
    try:
        return a.func(a)
    except TMError as e:
        print("Error: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
