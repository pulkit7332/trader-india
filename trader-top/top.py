#!/usr/bin/env python3
"""Top Setups for NSE/BSE day trades. Standard library only.

    python3 top.py                         # rescan, score, replay the top 10 -> trader_in/top.json
    python3 top.py --no-rescan             # reuse today's trader_in/watchlist.json (orb mode)
    python3 top.py --tm-top 5 --max-risk-pct 2
    python3 top.py --json

Other flags: --min-price 20, --filings-top 20, --max-confirm 200 (passed to
the scanner), --flat-by 15:15, --replays 10000, --seed 19, --now
"2026-10-29 10:15" (IST).

A rule-based ranking, in four stages, each one a sibling script of this pack
(nothing is re-implemented here, so every number matches what the single
skills would print):

  1. trader-scanner orb   every chart that closed above its opening range
  2. hold filter + fast score, on a fresh price for every hit (Yahoo spark,
     20 a request): keep only charts still ABOVE their range high, at least
     --min-price, whose opening-range-midpoint stop is no more than
     --max-risk-pct under the price. fast = day move % + % above range high
  3. trader-news (NSE filings only) on the best --filings-top: +2 to the
     fast score for an earnings, corporate-action or deal filing in 48 hours
  4. trader-exit + trader-timemachine on the best --tm-top: the default plan
     (entry at the current price, stop at the range midpoint, target 2R,
     flat by 15:15) replayed 10,000 times. Final order: average R (which
     counts the replays that hit neither side at their cutoff price, so it
     still means something late in the day), then target-first odds.

Breadth: the share of scanned charts up on the day (the scanner's
advancers / decliners, at the pinned clock). At BROAD_UP or more the
replays, drawn from ~60 ordinary sessions, tend to understate how far
breakouts run, so the order means less; at BROAD_DOWN or less every long
breakout is fighting the whole market. Both print a warning.

A rank is the output of that rule, not a recommendation. Long side only.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; trader-india-skill/1.0)"
PAUSE = 0.3
BACKOFF = 2.0
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
DATA_DIR = "trader_in"
OPEN = datetime.time(9, 15)
DEFAULT_FLAT_BY = "15:15"
FILING_TAGS = ("earnings", "corporate-action", "deal")
WARNING_TAGS = ("halt", "risk", "regulatory")
FILING_BONUS = 2.0
BROAD_UP = 70.0    # % of charts up on the day
BROAD_DOWN = 30.0

NSE_HOLIDAYS = {
    "2026-01-26", "2026-02-19", "2026-03-31", "2026-04-02", "2026-04-10", "2026-04-14",
    "2026-05-01", "2026-06-27", "2026-08-15", "2026-08-27", "2026-10-02", "2026-10-20",
    "2026-10-21", "2026-11-05", "2026-12-25",
}

PACK_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCAN = os.path.join(PACK_DIR, "trader-scanner", "scan.py")
NEWS = os.path.join(PACK_DIR, "trader-news", "news.py")
EXIT = os.path.join(PACK_DIR, "trader-exit", "exit.py")
TIMEMACHINE = os.path.join(PACK_DIR, "trader-timemachine", "timemachine.py")


class TopError(Exception):
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
        raise TopError('--now wants a time like "2026-10-29 10:15" (IST) or ...Z (UTC)')
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


# ---------------------------------------------------------------- network

def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except urllib.error.URLError as e:
        raise TopError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def fetch_prices(symbols, now_utc):
    """{SYMBOL: {"price", "prev_close"}} from Yahoo spark, 20 a request: the price is the close
    of the last 5-minute bar fully closed by now_utc. Missing on failure."""
    now_s = (now_utc - datetime.datetime(1970, 1, 1)).total_seconds()
    out = {}
    for i in range(0, len(symbols), 20):
        chunk = [yahoo_symbol(s) for s in symbols[i:i + 20]]
        if i:
            time.sleep(PAUSE)
        text = None
        for host in YAHOO_HOSTS:
            url = "https://%s/v7/finance/spark?symbols=%s&range=1d&interval=5m" % (host, ",".join(chunk))
            code, text = _get(url)
            if code == 429:
                time.sleep(BACKOFF)
                code, text = _get(url)
            if code == 200:
                break
            text = None
            if code != 429 and code < 500:
                break
        if not text:
            continue
        try:
            results = json.loads(text).get("spark", {}).get("result", [])
        except ValueError:
            continue
        for r in results:
            resp = (r.get("response") or [{}])[0]
            meta = resp.get("meta", {})
            closes = ((resp.get("indicators", {}).get("quote") or [{}])[0]).get("close") or []
            price = None
            for t, c in zip(resp.get("timestamp") or [], closes):
                if t is None or t + 300 > now_s:
                    break
                if c is not None:
                    price = c
            prev = meta.get("previousClose") or meta.get("chartPreviousClose")
            if price and prev:
                out[bare_symbol(r.get("symbol", ""))] = {"price": price, "prev_close": prev}
    return out


# ---------------------------------------------------------------- sibling skills

def run_sibling(script, args, timeout=900):
    """Run a sibling skill's script with --json; -> (parsed JSON or None, the text it printed)."""
    if not os.path.exists(script):
        raise TopError("missing %s; trader-top needs the rest of the trader-india pack beside it" % script)
    p = subprocess.run([sys.executable, script] + args, capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(p.stdout), p.stdout.strip()
    except ValueError:
        return None, (p.stdout.strip() or p.stderr.strip())


def load_watchlist(a, local):
    if a.no_rescan:
        path = os.path.join(DATA_DIR, "watchlist.json")
        if not os.path.exists(path):
            raise TopError("no %s; drop --no-rescan" % path)
        with open(path, encoding="utf-8") as fh:
            wl = json.load(fh)
        if wl.get("mode") != "orb" or not str(wl.get("as_of", "")).startswith(local.date().isoformat()):
            raise TopError("%s is not today's opening-range scan (%s, %s); drop --no-rescan"
                           % (path, wl.get("mode"), wl.get("as_of")))
        return wl
    args = ["orb", "--json", "--max-confirm", str(a.max_confirm)]
    if a.now:
        args += ["--now", a.now]
    wl, text = run_sibling(SCAN, args)
    if wl is None:
        raise TopError("trader-scanner: %s" % (text.splitlines()[-1] if text else "no output"))
    return wl


def filings_for(symbols):
    """{SYMBOL: [tags...]} from NSE filings in the last 48 hours (best effort; {} if NSE refuses)."""
    if not symbols:
        return {}, []
    args = ["--tickers"] + symbols + ["--hours", "48", "--no-yahoo", "--all", "--dry-run", "--json"]
    doc, text = run_sibling(NEWS, args)
    if doc is None:
        return {}, ["trader-news: %s" % (text.splitlines()[-1] if text else "no output")]
    tags = {}
    for item in doc.get("items", []):
        tags.setdefault(item["symbol"], set()).update(item.get("tags", []))
    return {s: sorted(t) for s, t in tags.items()}, doc.get("errors", [])


# ---------------------------------------------------------------- the ranking

def cmd_top(a):
    now_utc = parse_now(a.now)
    local = to_ist(now_utc)
    hhmm = local.strftime("%H:%M")
    if local.weekday() >= 5 or local.date().isoformat() in NSE_HOLIDAYS:
        raise TopError("NSE is closed today (%s); nothing to rank" % local.date().isoformat())
    range_end = (datetime.datetime.combine(local.date(), OPEN) + datetime.timedelta(minutes=15)).time()
    if local.time() < range_end:
        raise TopError("the opening range is still forming; run after %s IST" % range_end.strftime("%H:%M"))
    if hhmm >= a.flat_by:
        raise TopError("it is %s IST, at or after flat-by %s; too late to rank new entries today" % (hhmm, a.flat_by))

    wl = load_watchlist(a, local)
    adv, dec = wl.get("advancers"), wl.get("decliners")
    breadth = round(adv / (adv + dec) * 100, 1) if adv is not None and adv + dec else None
    hits = wl.get("hits", [])
    if not hits:
        raise TopError("no chart closed above its opening range (%d usable)" % wl.get("usable", 0))

    prices = fetch_prices([h["symbol"] for h in hits], now_utc)
    dropped = {"no_price": 0, "under_min_price": 0, "back_in_range": 0, "stop_too_wide": 0}
    pool = []
    for h in hits:
        q = prices.get(h["symbol"])
        if not q:
            dropped["no_price"] += 1
            continue
        price, hi, lo = q["price"], h["range_high"], h["range_low"]
        if price < a.min_price:
            dropped["under_min_price"] += 1
            continue
        if price <= hi:
            dropped["back_in_range"] += 1
            continue
        risk_pct = (price - (hi + lo) / 2.0) / price * 100
        if risk_pct > a.max_risk_pct:
            dropped["stop_too_wide"] += 1
            continue
        day = (price / q["prev_close"] - 1) * 100
        above = (price / hi - 1) * 100
        pool.append({"symbol": h["symbol"], "price": round(price, 2), "range_high": hi, "range_low": lo,
                     "broke_at": h["time"], "day_move_pct": round(day, 2), "above_range_pct": round(above, 2),
                     "risk_pct": round(risk_pct, 2), "fast_score": round(day + above, 2)})
    pool.sort(key=lambda c: -c["fast_score"])

    shortlist = pool[: a.filings_top]
    tags, news_errors = filings_for([c["symbol"] for c in shortlist])
    for c in shortlist:
        t = tags.get(c["symbol"], [])
        c["filing_tags"] = t
        c["warnings"] = [x for x in t if x in WARNING_TAGS]
        if any(x in FILING_TAGS for x in t):
            c["fast_score"] = round(c["fast_score"] + FILING_BONUS, 2)
    shortlist.sort(key=lambda c: -c["fast_score"])

    entry_bar = ((local.hour * 60 + local.minute) - (OPEN.hour * 60 + OPEN.minute)) // 5 - 1
    ranked, skipped = [], []
    for c in shortlist[: a.tm_top]:
        common = ["--now", a.now] if a.now else []
        plan, text = run_sibling(EXIT, ["plan", "--symbol", c["symbol"], "--entry", str(c["price"]),
                                        "--or-high", str(c["range_high"]), "--or-low", str(c["range_low"]),
                                        "--flat-by", a.flat_by, "--json"] + common)
        if plan is None:
            skipped.append("%s: %s" % (c["symbol"], text))
            continue
        tm, text = run_sibling(TIMEMACHINE, ["run", "--symbol", c["symbol"], "--date", local.date().isoformat(),
                                             "--entry", str(plan["entry"]), "--stop", str(plan["stop"]),
                                             "--target", str(plan["target"]), "--entry-bar", str(entry_bar),
                                             "--entry-time", hhmm, "--until", a.flat_by,
                                             "--replays", str(a.replays), "--seed", str(a.seed), "--json"] + common)
        if tm is None:
            skipped.append("%s: %s" % (c["symbol"], text))
            continue
        c.update({"stop": plan["stop"], "target": plan["target"], "risk_per_share": plan["risk_per_share"],
                  "mc": tm["mc"], "history": tm["history"], "verdict": tm["verdict"]})
        ranked.append(c)
    ranked.sort(key=lambda c: (-c["mc"]["avg_r"], -c["mc"]["target_pct"]))

    doc = {"as_of": local.strftime("%Y-%m-%d %H:%M IST"), "scan_as_of": wl.get("as_of"),
           "rule": {"min_price": a.min_price, "max_risk_pct": a.max_risk_pct, "filings_top": a.filings_top,
                    "tm_top": a.tm_top, "stop": "or-mid", "target": "2R", "flat_by": a.flat_by,
                    "replays": a.replays, "seed": a.seed},
           "scan": {k: wl.get(k) for k in ("universe", "scanned", "failed", "candidates", "confirmed_checked", "usable")},
           "breadth": {"advancers": adv, "decliners": dec, "up_pct": breadth},
           "breakouts": len(hits), "dropped": dropped, "holding": len(pool), "ranked": ranked,
           "skipped": skipped, "news_errors": news_errors}
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "top.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)

    if a.json:
        print(json.dumps(doc, indent=2))
        return 0 if ranked else 1
    s = doc["scan"]
    print("Scan %s: %s symbols, %s usable charts checked, %d closed above their opening range."
          % (doc["scan_as_of"], s["universe"], s["usable"], len(hits)))
    print("At %s IST, %d still hold above the range high with a stop within %.1f%% (dropped: %d back in range, "
          "%d stop too wide, %d under ₹%g, %d no price)."
          % (hhmm, len(pool), a.max_risk_pct, dropped["back_in_range"], dropped["stop_too_wide"],
             dropped["under_min_price"], a.min_price, dropped["no_price"]))
    if breadth is not None:
        print("Breadth: %.0f%% of %d charts up on the day (%d up, %d down)." % (breadth, adv + dec, adv, dec))
        if breadth >= BROAD_UP:
            print("  ! Broad up day. The replays come from ~60 ordinary sessions and tend to understate how far "
                  "breakouts run on a day like this; the rank order means less than usual.")
        elif breadth <= BROAD_DOWN:
            print("  ! Broad down day. Every long breakout here is fighting the whole market; the replays may "
                  "overstate its odds.")
    if not ranked:
        print("Nothing was replayed.")
    else:
        print("Ranked by average R over %d replays (seed %d), entry now, stop at the range midpoint, "
              "target 2R, flat by %s:" % (a.replays, a.seed, a.flat_by))
        print("  #  symbol          price      stop    target  risk  target   stop neither  avg R  history T/S/N  filings")
        for i, c in enumerate(ranked, 1):
            m, hst = c["mc"], c["history"]
            hist = "%d: %d/%d/%d" % (hst["fired"], hst["target_first"], hst["stop_first"], hst["neither"])
            print("  %-2d %-12s %9.2f %9.2f %9.2f %4.1f%% %6.1f%% %5.1f%% %6.1f%%  %+.2f  %-13s  %s"
                  % (i, c["symbol"], c["price"], c["stop"], c["target"], c["risk_pct"], m["target_pct"],
                     m["stop_pct"], m["neither_pct"], m["avg_r"], hist, ",".join(c["filing_tags"]) or "-"))
        if sum(c["mc"]["neither_pct"] for c in ranked) / len(ranked) > 50:
            print("  Most replays hit neither side before %s: too little session left for a 2R target. "
                  "Treat this list as weak." % a.flat_by)
        for c in ranked:
            if c["warnings"]:
                print("  ! %s has a %s filing in the last 48 hours; read it first." % (c["symbol"], "/".join(c["warnings"])))
    for line in skipped:
        print("  skipped %s" % line)
    if news_errors:
        print("  NSE filings were partly unavailable (%d errors); the filing bonus may be missing." % len(news_errors))
    print("A rank is a rules output from delayed data, not a recommendation; prices move on. "
          "Pick one yourself, then /trader-exit, /trader-budget, /trader-risk.")
    return 0 if ranked else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE top setups: scan, score, replay")
    p.add_argument("--no-rescan", action="store_true")
    p.add_argument("--max-confirm", type=int, default=200)
    p.add_argument("--min-price", type=float, default=20.0)
    p.add_argument("--max-risk-pct", type=float, default=3.0)
    p.add_argument("--filings-top", type=int, default=20)
    p.add_argument("--tm-top", type=int, default=10)
    p.add_argument("--flat-by", default=DEFAULT_FLAT_BY)
    p.add_argument("--replays", type=int, default=10000)
    p.add_argument("--seed", type=int, default=19)
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        return cmd_top(a)
    except TopError as e:
        print("Refused: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
