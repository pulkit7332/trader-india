#!/usr/bin/env python3
"""Chart Scanner for NSE/BSE cash equities. Standard library only.

    python3 scan.py universe                       # -> trader_in/universe.txt
    python3 scan.py daily                           # before 9:15 IST -> trader_in/watchlist.json
    python3 scan.py daily --symbols RELIANCE,TCS,INFY
    python3 scan.py orb                             # after 9:35 IST -> trader_in/watchlist.json
    python3 scan.py orb --at 10:15

Setups on daily closes (Yahoo spark has no highs/lows):
  breakout_20d   last close above every close of the 20 sessions before it
  bull_flag      up >10% over the 10 sessions before the last 5, then the
                 last 5 closes sit inside a 5% range
  reclaim_ma20   close before was under its 20-day average, last close is above it
  oversold       RSI 14 (Wilder) under 30
  oversold_bounce RSI 14 was under 30 yesterday and today closed up

Opening-range breakout (orb): the opening range is the first --or-minutes
(15) of the session: the 9:15, 9:20 and 9:25 five-minute bars on the NSE
cash market (session 09:15-15:30 IST, pre-open 09:00-09:08, no DST, no
early closes). A hit is the first completed 5-minute bar to CLOSE above the
range high.

Symbols are NSE tickers without a suffix (RELIANCE); BSE tickers end .BO.
This tool appends .NS for Yahoo lookups unless a symbol already ends in
.NS or .BO.

Data sources: Yahoo's chart/spark endpoints (free, unofficial, can lag;
query2 first, query1 on a 429 or 5xx) and NSE's own archived EQUITY_L.csv
symbol list (archives.nseindia.com; this static archive host has been far
more reliable than nseindia.com's live API, which rate-limits and
sometimes blocks scripted access from outside India).
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; trader-india-skill/1.0; +https://github.com/)"
PAUSE = 0.3
BACKOFF = 2.0
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
EQUITY_L_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
DATA_DIR = "trader_in"

# ---------------------------------------------------------------- NSE calendar (IST, no DST)

NSE_HOLIDAYS = {
    "2026-01-26": "Republic Day",
    "2026-02-19": "Chhatrapati Shivaji Maharaj Jayanti",
    "2026-03-31": "Id-Ul-Fitr (Eid)",
    "2026-04-02": "Ram Navami",
    "2026-04-10": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-06-27": "Id-Ul-Adha (Bakri Id)",
    "2026-08-15": "Independence Day",
    "2026-08-27": "Ganesh Chaturthi",
    "2026-10-02": "Gandhi Jayanti / Dussehra",
    "2026-10-20": "Diwali Laxmi Pujan (cash market closed; Muhurat trading only, evening)",
    "2026-10-21": "Diwali Balipratipada",
    "2026-11-05": "Guru Nanak Jayanti",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)
PRE_OPEN_START = datetime.time(9, 0)
PRE_OPEN_END = datetime.time(9, 8)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)


class ScanError(Exception):
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
        raise ScanError('--now wants a time like "2026-10-19 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    """(is_trading_day, reason). Weekends and the NSE/BSE equity holiday list."""
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NSE/BSE holiday: " + name
    return True, ""


def session(d):
    ok, _ = trading_day(d)
    return (OPEN, CLOSE) if ok else None


def market_status(now_utc):
    local = to_ist(now_utc)
    d = local.date()
    ok, reason = trading_day(d)
    st = {"now_ist": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(),
          "weekday": local.strftime("%A"), "calendar_known": d.year in CALENDAR_YEARS,
          "open": False, "reason": reason}
    s = session(d)
    if s:
        o, c = s
        st["opens"], st["closes"] = o.strftime("%H:%M"), c.strftime("%H:%M")
        if o <= local.time() < c:
            st["open"] = True
            st["reason"] = "regular session"
        elif local.time() < o:
            st["reason"] = "before the open"
        else:
            st["reason"] = "after the close"
    return st


# ---------------------------------------------------------------- symbols

def yahoo_symbol(sym):
    sym = sym.upper().strip()
    if sym.endswith(".NS") or sym.endswith(".BO"):
        return sym
    return sym + ".NS"


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
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        finally:
            try:
                e.close()
            except Exception:
                pass
        return e.code, body
    except urllib.error.URLError as e:
        raise ScanError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def fetch_yahoo_path(path_for_host, timeout=20):
    """path_for_host(host) -> url. query2 first, one retry on 429, then query1."""
    last = None
    for host in YAHOO_HOSTS:
        url = path_for_host(host)
        code, text = _get(url, timeout)
        if code == 429:
            time.sleep(BACKOFF)
            code, text = _get(url, timeout)
        if code == 200:
            return text
        last = (url, code)
        if code != 429 and code < 500:
            break
    raise ScanError("%s answered HTTP %d" % (last[0].split("?")[0], last[1]))


def spark_url(host, symbols, rng="2mo"):
    return "https://%s/v7/finance/spark?symbols=%s&range=%s&interval=1d" % (host, ",".join(symbols), rng)


def v8_url(host, symbol, rng="1d", interval="5m"):
    return "https://%s/v8/finance/chart/%s?range=%s&interval=%s" % (host, symbol, rng, interval)


# ---------------------------------------------------------------- universe

def fetch_universe(offline=None):
    if offline:
        with open(offline, encoding="utf-8") as fh:
            text = fh.read()
    else:
        code, text = _get(EQUITY_L_URL)
        if code != 200:
            raise ScanError("NSE's EQUITY_L.csv answered HTTP %d" % code)
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        series = r.get("SERIES", "")
        if series != "EQ":
            # BE/BZ are trade-for-trade (no intraday netting; settlement is
            # delivery-only), so they cannot be day-traded. Keep only EQ.
            continue
        sym = r.get("SYMBOL", "")
        name = r.get("NAME OF COMPANY", "")
        if sym:
            out.append((sym, name))
    return out


def cmd_universe(a):
    try:
        rows = fetch_universe(a.offline)
    except ScanError as e:
        print("Could not build the universe: %s" % e)
        return 1
    if not a.no_write:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "universe.txt"), "w", encoding="utf-8") as fh:
            for sym, name in rows:
                fh.write("%s|%s\n" % (sym, name))
    print("%d NSE cash-equity symbols (series EQ only; BE/BZ trade-for-trade symbols excluded)%s"
          % (len(rows), "" if a.no_write else (" -> %s/universe.txt" % DATA_DIR)))
    return 0 if rows else 1


def read_universe(path=None):
    path = path or os.path.join(DATA_DIR, "universe.txt")
    if not os.path.exists(path):
        raise ScanError("no %s; run: scan.py universe" % path)
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            sym, _, name = line.partition("|")
            out.append((sym, name))
    return out


# ---------------------------------------------------------------- RSI / setups

def rsi14(closes):
    if len(closes) < 15:
        return None
    gains, losses = [], []
    for i in range(1, 15):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_gain = sum(gains) / 14.0
    avg_loss = sum(losses) / 14.0
    for i in range(15, len(closes)):
        chg = closes[i] - closes[i - 1]
        gain = max(chg, 0.0)
        loss = max(-chg, 0.0)
        avg_gain = (avg_gain * 13 + gain) / 14.0
        avg_loss = (avg_loss * 13 + loss) / 14.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def daily_setups(closes):
    """closes: oldest first, today last. -> list of setup names that fire today."""
    hits = []
    if len(closes) >= 21 and closes[-1] > max(closes[-21:-1]):
        hits.append("breakout_20d")
    if len(closes) >= 16:
        base = closes[-16:-5]
        last5 = closes[-5:]
        if base[0] and (base[-1] / base[0] - 1.0) * 100 > 10.0:
            lo, hi = min(last5), max(last5)
            if lo and (hi - lo) / lo * 100 <= 5.0:
                hits.append("bull_flag")
    if len(closes) >= 21:
        ma_now = sum(closes[-20:]) / 20.0
        ma_prev = sum(closes[-21:-1]) / 20.0
        if closes[-2] < ma_prev and closes[-1] > ma_now:
            hits.append("reclaim_ma20")
    r_today = rsi14(closes)
    r_yday = rsi14(closes[:-1]) if len(closes) > 15 else None
    if r_today is not None and r_today < 30:
        hits.append("oversold")
    if r_yday is not None and r_yday < 30 and closes[-1] > closes[-2]:
        hits.append("oversold_bounce")
    return hits


def cmd_daily(a):
    now_utc = parse_now(a.now)
    if a.symbols:
        syms = [bare_symbol(s) for s in a.symbols.split(",") if s.strip()]
    elif a.watchlist:
        with open(a.watchlist, encoding="utf-8") as fh:
            wl = json.load(fh)
        syms = [bare_symbol(h["symbol"]) for h in wl.get("hits", [])]
    else:
        syms = [s for s, _ in read_universe()]
    if a.limit:
        syms = syms[: a.limit]
    if not syms:
        print("No symbols to scan.")
        return 1

    scanned, failed, hits = 0, 0, []
    batch = 20
    for i in range(0, len(syms), batch):
        chunk = [yahoo_symbol(s) for s in syms[i:i + batch]]
        if i:
            time.sleep(PAUSE)
        try:
            text = fetch_yahoo_path(lambda h, c=chunk: spark_url(h, c))
        except ScanError:
            failed += len(chunk)
            continue
        try:
            data = json.loads(text)
        except ValueError:
            failed += len(chunk)
            continue
        results = data.get("spark", {}).get("result", [])
        failed += max(0, len(chunk) - len(results))  # Yahoo silently drops a renamed/delisted symbol
        for r in results:
            sym = bare_symbol(r.get("symbol", ""))
            resp = (r.get("response") or [{}])[0]
            meta = resp.get("meta", {})
            q = (resp.get("indicators", {}).get("quote") or [{}])[0]
            closes = [c for c in (q.get("close") or []) if c is not None]
            if not closes:
                failed += 1
                continue
            scanned += 1
            price = meta.get("regularMarketPrice") or closes[-1]
            volume = meta.get("regularMarketVolume") or 0
            if price < a.min_price or volume < a.min_volume:
                continue
            setups = daily_setups(closes)
            if setups:
                hits.append({"symbol": sym, "price": round(price, 2), "volume": volume,
                             "setups": setups, "dollar_volume": round(price * volume, 2)})

    hits.sort(key=lambda h: -h["dollar_volume"])
    doc = {"mode": "daily", "as_of": to_ist(now_utc).strftime("%Y-%m-%d %H:%M IST"),
           "universe": len(syms), "scanned": scanned, "failed": failed,
           "min_price": a.min_price, "min_volume": a.min_volume, "hits": hits}
    if not a.no_write:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "watchlist.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        print("Fetched %d of %d charts (%d failed or had no data); %d liquid at or above ₹%s / %s shares."
              % (scanned + failed, len(syms), failed, len(hits), a.min_price, a.min_volume))
        for h in hits[: a.show]:
            print("  %-12s ₹%-10.2f vol %-12d %s" % (h["symbol"], h["price"], h["volume"], ", ".join(h["setups"])))
    return 0 if hits else 1


# ---------------------------------------------------------------- opening-range breakout

def or_bars_needed(or_minutes):
    return max(1, or_minutes // 5)


EPOCH = datetime.datetime(1970, 1, 1)


def closed_bar_index(ts, now_utc, interval_minutes=5):
    """How many of the leading bars in ts (chronological) are fully closed by now_utc."""
    now_s = (now_utc - EPOCH).total_seconds()
    n = 0
    for t in ts:
        if t is None or t + interval_minutes * 60 > now_s:
            break
        n += 1
    return n


def closed_bars(ts, values, now_utc, interval_minutes=5):
    """values up to the closed-bar cutoff, with Nones dropped. Same order as ts."""
    n = closed_bar_index(ts, now_utc, interval_minutes)
    return [v for v in values[:n] if v is not None]


def cmd_orb(a):
    now_utc = parse_now(a.now)
    local = to_ist(now_utc)
    st = market_status(now_utc)
    if not st["open"] and st["reason"] not in ("after the close",):
        print("Market is not open (%s). ORB scan needs live bars." % st["reason"])
        return 1
    cutoff = a.at
    n_range = or_bars_needed(a.or_minutes)
    range_end_minutes = n_range * 5
    range_end = (datetime.datetime.combine(local.date(), OPEN) + datetime.timedelta(minutes=range_end_minutes)).time()
    if local.time() < range_end and not a.offline:
        print("Opening range (%d minutes) is still forming; check back after %s IST." % (a.or_minutes, range_end.strftime("%H:%M")))
        return 1

    if a.symbols:
        syms = [bare_symbol(s) for s in a.symbols.split(",") if s.strip()]
    elif a.watchlist:
        with open(a.watchlist, encoding="utf-8") as fh:
            wl = json.load(fh)
        syms = [bare_symbol(h["symbol"]) for h in wl.get("hits", [])]
    else:
        syms = [s for s, _ in read_universe()]
    if a.limit:
        syms = syms[: a.limit]

    scanned, failed, candidates = 0, 0, []
    batch = 20
    for i in range(0, len(syms), batch):
        chunk = [yahoo_symbol(s) for s in syms[i:i + batch]]
        if i:
            time.sleep(PAUSE)
        try:
            text = fetch_yahoo_path(
                lambda h, c=chunk: "https://%s/v7/finance/spark?symbols=%s&range=1d&interval=5m" % (h, ",".join(c)))
        except ScanError:
            failed += len(chunk)
            continue
        try:
            data = json.loads(text)
        except ValueError:
            failed += len(chunk)
            continue
        results = data.get("spark", {}).get("result", [])
        failed += max(0, len(chunk) - len(results))
        for r in results:
            sym = bare_symbol(r.get("symbol", ""))
            resp = (r.get("response") or [{}])[0]
            q = (resp.get("indicators", {}).get("quote") or [{}])[0]
            closes = closed_bars(resp.get("timestamp") or [], q.get("close") or [], now_utc)
            if len(closes) <= n_range:
                failed += 1
                continue
            scanned += 1
            range_high = max(closes[:n_range])
            post = closes[n_range:]
            if any(c >= range_high for c in post):
                meta = resp.get("meta", {})
                price = meta.get("regularMarketPrice") or closes[-1]
                vol = meta.get("regularMarketVolume") or 0
                candidates.append({"symbol": sym, "price": price, "dollar_volume": price * vol})

    candidates.sort(key=lambda c: -c["dollar_volume"])
    max_confirm = len(candidates) if a.max_confirm == 0 else a.max_confirm
    confirmed, usable = [], 0
    to_confirm = candidates[:max_confirm]
    for i, c in enumerate(to_confirm):
        if i:
            time.sleep(PAUSE)
        sym = c["symbol"]
        try:
            text = fetch_yahoo_path(lambda h, s=yahoo_symbol(sym): v8_url(h, s, rng="1d", interval="5m"))
            data = json.loads(text)
            res = data["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            ts_full = res.get("timestamp") or []
            n_closed = closed_bar_index(ts_full, now_utc)
            highs = (q.get("high") or [])[:n_closed]
            lows = (q.get("low") or [])[:n_closed]
            closes = (q.get("close") or [])[:n_closed]
            ts = ts_full[:n_closed]
        except Exception:
            continue
        if len(closes) <= n_range or any(v is None for v in highs[:n_range]) or any(v is None for v in lows[:n_range]):
            continue
        usable += 1
        range_high = max(highs[:n_range])
        range_low = min(lows[:n_range])
        trigger = None
        for j in range(n_range, len(closes)):
            if closes[j] is None:
                continue
            if closes[j] > range_high:
                bar_close_utc = datetime.datetime.utcfromtimestamp(ts[j]) + datetime.timedelta(minutes=5)
                trigger = {"bar": j, "price": round(closes[j], 2),
                           "time": to_ist(bar_close_utc).strftime("%H:%M")}
                break
        if trigger:
            confirmed.append({"symbol": sym, "range_high": round(range_high, 2),
                               "range_low": round(range_low, 2), **trigger,
                               "over_pct": round((trigger["price"] / range_high - 1) * 100, 2)})

    confirmed.sort(key=lambda c: c["time"])
    doc = {"mode": "orb", "as_of": local.strftime("%Y-%m-%d %H:%M IST"), "or_minutes": a.or_minutes,
           "universe": len(syms), "scanned": scanned, "failed": failed, "candidates": len(candidates),
           "confirmed_checked": len(to_confirm), "usable": usable, "hits": confirmed}
    if not a.no_write:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "watchlist.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        partial = " (PARTIAL: %d candidates, %d checked)" % (len(candidates), len(to_confirm)) if len(candidates) > len(to_confirm) else ""
        print("Fetched %d of %d charts (%d failed or had no data); %d usable; %d candidates%s."
              % (scanned + failed, len(syms), failed, usable, len(candidates), partial))
        print("%d of %d usable charts closed above their opening-range high by %s IST:" % (len(confirmed), usable, local.strftime("%H:%M")))
        for c in confirmed[: a.show]:
            print("  %5s  %-12s ₹%-10.2f range %.2f to %.2f  %+.2f%% over"
                  % (c["time"], c["symbol"], c["price"], c["range_low"], c["range_high"], c["over_pct"]))
    return 0 if confirmed else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE chart scanner")
    sub = p.add_subparsers(dest="cmd", required=True)

    pu = sub.add_parser("universe")
    pu.add_argument("--offline")
    pu.add_argument("--no-write", action="store_true")
    pu.set_defaults(func=cmd_universe)

    pd = sub.add_parser("daily")
    pd.add_argument("--symbols")
    pd.add_argument("--watchlist")
    pd.add_argument("--limit", type=int, default=0)
    pd.add_argument("--min-price", type=float, default=20.0)
    pd.add_argument("--min-volume", type=float, default=100000)
    pd.add_argument("--now")
    pd.add_argument("--no-write", action="store_true")
    pd.add_argument("--json", action="store_true")
    pd.add_argument("--show", type=int, default=20)
    pd.set_defaults(func=cmd_daily)

    po = sub.add_parser("orb")
    po.add_argument("--symbols")
    po.add_argument("--watchlist")
    po.add_argument("--limit", type=int, default=0)
    po.add_argument("--or-minutes", type=int, default=15)
    po.add_argument("--max-confirm", type=int, default=200)
    po.add_argument("--at")
    po.add_argument("--now")
    po.add_argument("--offline")
    po.add_argument("--no-write", action="store_true")
    po.add_argument("--json", action="store_true")
    po.add_argument("--show", type=int, default=20)
    po.set_defaults(func=cmd_orb)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except ScanError as e:
        print("Error: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
