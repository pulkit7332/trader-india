#!/usr/bin/env python3
"""News Monitor for NSE/BSE watchlists. Standard library only.

    python3 news.py                            # trader_in/watchlist.json + plan + portfolio
    python3 news.py --tickers RELIANCE TCS --hours 12
    python3 news.py --no-nse --json
    python3 news.py --all --dry-run

Two feeds, both best-effort:
  NSE corporate announcements  https://www.nseindia.com/api/corporate-announcements
      (official filings: results, board meetings, disclosures. This is
      NSE's LIVE api host, which is known to rate-limit or block scripted
      access, especially from outside India or from data-center IPs. This
      tool primes a session cookie first and fails soft, per symbol, when
      blocked -- never crashes the whole run over one blocked ticker.)
  Yahoo Finance search news    https://query1.finance.yahoo.com/v1/finance/search
      (best-effort: Yahoo's news corpus for NSE tickers is thin and the
      search is a fuzzy text match, so results are filtered to items whose
      own "relatedTickers" actually names the <SYMBOL>.NS ticker. Expect
      fewer hits than the US pack's SEC EDGAR + Yahoo headline RSS combo --
      there is no Indian EDGAR equivalent.)

Price move: Yahoo chart meta, same solid source trader-scanner and
trader-alerts use. Computed and reported in IST.
"""

import argparse
import datetime
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; trader-india-skill/1.0)"
NSE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
DATA_DIR = "trader_in"
PAUSE = 0.4


class NewsError(Exception):
    pass


def to_ist(utc_naive):
    return utc_naive + datetime.timedelta(hours=5, minutes=30)


def now_utc_naive():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def yahoo_symbol(sym):
    sym = sym.upper().strip()
    return sym if sym.endswith(".NS") or sym.endswith(".BO") else sym + ".NS"


def bare_symbol(sym):
    sym = sym.upper().strip()
    for suf in (".NS", ".BO"):
        if sym.endswith(suf):
            return sym[: -len(suf)]
    return sym


# ---------------------------------------------------------------- price move (solid)

def fetch_move(sym):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=1d" % yahoo_symbol(sym)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev = meta.get("previousClose") or meta.get("chartPreviousClose")
        if price is None or not prev:
            return None
        return {"price": price, "prev_close": prev, "move_pct": round((price / prev - 1.0) * 100, 2)}
    except Exception:
        return None


# ---------------------------------------------------------------- NSE corporate announcements (flaky, best-effort)

_cookie_opener = None


def nse_opener():
    global _cookie_opener
    if _cookie_opener is None:
        cj = http.cookiejar.CookieJar()
        _cookie_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        try:
            req = urllib.request.Request("https://www.nseindia.com/", headers={"User-Agent": NSE_UA, "Accept": "text/html"})
            _cookie_opener.open(req, timeout=15).read()
        except Exception:
            pass  # fails soft; the API call below will fail soft too if priming did not work
    return _cookie_opener


def fetch_nse_announcements(sym, timeout=15):
    """[] on any failure -- NSE's live API is the flaky source in this pack."""
    url = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s" % urllib.parse.quote(bare_symbol(sym))
    req = urllib.request.Request(url, headers={"User-Agent": NSE_UA, "Accept": "application/json"})
    try:
        with nse_opener().open(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        raise NewsError("NSE announcements: %s" % e)
    out = []
    for d in data:
        try:
            dt = datetime.datetime.strptime(d.get("an_dt", ""), "%d-%b-%Y %H:%M:%S")
        except ValueError:
            continue
        out.append({"symbol": bare_symbol(sym), "when_ist": dt.strftime("%Y-%m-%d %H:%M"),
                    "text": (d.get("attchmntText") or d.get("desc") or "").strip(),
                    "desc": d.get("desc", ""), "attachment": d.get("attchmntFile"),
                    "source": "nse_announcement", "id": "nse-%s-%s" % (bare_symbol(sym), dt.isoformat())})
    return out


# ---------------------------------------------------------------- Yahoo search news (best-effort)

def fetch_yahoo_news(sym, timeout=15):
    target = yahoo_symbol(sym)
    url = ("https://query1.finance.yahoo.com/v1/finance/search?q=%s&newsCount=15&quotesCount=0&lang=en-IN&region=IN"
           % urllib.parse.quote(bare_symbol(sym)))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        raise NewsError("Yahoo news search: %s" % e)
    out = []
    for n in data.get("news", []):
        related = n.get("relatedTickers") or []
        if target not in related:
            continue  # the bare-symbol search is a fuzzy text match; keep only confirmed hits
        ts = n.get("providerPublishTime")
        when = to_ist(datetime.datetime.utcfromtimestamp(ts)) if ts else None
        out.append({"symbol": bare_symbol(sym), "when_ist": when.strftime("%Y-%m-%d %H:%M") if when else "",
                    "text": n.get("title", ""), "link": n.get("link"), "publisher": n.get("publisher"),
                    "source": "yahoo_news", "id": "yahoo-%s" % n.get("uuid", n.get("link", ""))})
    return out


# ---------------------------------------------------------------- tagging

TAGS = [
    (re.compile(r"\bresult|\bearnings|\bquarter|\bq[1-4]\b|\bprofit|\brevenue", re.I), "earnings"),
    (re.compile(r"\bboard meeting|\bagm|\begm", re.I), "corporate-action"),
    (re.compile(r"\bdividend|\bbonus|\bsplit|\bbuyback|\brights issue", re.I), "capital"),
    (re.compile(r"\bresign|\bappoint|\bdirector|\bceo|\bcfo|\bmanaging director", re.I), "people"),
    (re.compile(r"\bmerger|\bacquisition|\bstake|\bjoint venture|\bslump sale", re.I), "deal"),
    (re.compile(r"\bsebi|\brbi|\bregulatory|\bpenalty|\bshow cause", re.I), "regulatory"),
    (re.compile(r"\bdowngrade|\bupgrade|\btarget price|\brating", re.I), "analyst"),
    (re.compile(r"\bhalt|\bsuspend|\bcircuit", re.I), "halt"),
    (re.compile(r"\bfraud|\bdefault|\binsolvency|\bliquidat", re.I), "risk"),
]


def tag_item(item):
    text = (item.get("text", "") + " " + item.get("desc", "")).lower()
    tags = [name for pat, name in TAGS if pat.search(text)]
    return tags or ["other"]


# ---------------------------------------------------------------- state (dedup)

def load_state():
    path = os.path.join(DATA_DIR, "state", "news.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (ValueError, OSError):
            pass
    return {"seen": {}}


def save_state(state):
    os.makedirs(os.path.join(DATA_DIR, "state"), exist_ok=True)
    path = os.path.join(DATA_DIR, "state", "news.json")
    cutoff = (now_utc_naive() - datetime.timedelta(days=30)).isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v > cutoff}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


# ---------------------------------------------------------------- main

def symbols_from_files():
    syms = []
    wl_path = os.path.join(DATA_DIR, "watchlist.json")
    if os.path.exists(wl_path):
        with open(wl_path, encoding="utf-8") as fh:
            wl = json.load(fh)
        syms += [h["symbol"] for h in wl.get("hits", [])]
    plan_path = os.path.join(DATA_DIR, "plan.json")
    if os.path.exists(plan_path):
        with open(plan_path, encoding="utf-8") as fh:
            plan = json.load(fh)
        if plan.get("symbol"):
            syms.append(plan["symbol"])
    port_path = os.path.join(DATA_DIR, "portfolio.json")
    if os.path.exists(port_path):
        with open(port_path, encoding="utf-8") as fh:
            port = json.load(fh)
        syms += list((port.get("positions") or {}).keys())
    seen, out = set(), []
    for s in syms:
        s = bare_symbol(s)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE news monitor")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--move-pct", type=float, default=3.0)
    p.add_argument("--no-nse", action="store_true", help="skip NSE corporate announcements")
    p.add_argument("--no-yahoo", action="store_true", help="skip Yahoo news search")
    p.add_argument("--all", action="store_true", help="show items already seen")
    p.add_argument("--dry-run", action="store_true", help="do not update the seen-state file")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    syms = [bare_symbol(s) for s in a.tickers] if a.tickers else symbols_from_files()
    if not syms:
        print("No tickers: pass --tickers, or run trader-scanner / trader-exit / trader-broker first.")
        return 1

    state = load_state()
    cutoff_dt = now_utc_naive() - datetime.timedelta(hours=a.hours)
    items, errors, moves = [], [], {}

    for i, sym in enumerate(syms):
        if i:
            time.sleep(PAUSE)
        mv = fetch_move(sym)
        if mv:
            moves[sym] = mv
        if not a.no_nse:
            try:
                items += fetch_nse_announcements(sym)
            except NewsError as e:
                errors.append(str(e))
        if not a.no_yahoo:
            try:
                items += fetch_yahoo_news(sym)
            except NewsError as e:
                errors.append(str(e))

    fresh = []
    for it in items:
        if not it.get("when_ist"):
            continue
        try:
            when = datetime.datetime.strptime(it["when_ist"], "%Y-%m-%d %H:%M") - datetime.timedelta(hours=5, minutes=30)
        except ValueError:
            continue
        if when < cutoff_dt:
            continue
        it["tags"] = tag_item(it)
        it["new"] = it["id"] not in state["seen"]
        if it["new"] or a.all:
            fresh.append(it)
        if it["new"] and not a.dry_run:
            state["seen"][it["id"]] = now_utc_naive().isoformat()

    fresh.sort(key=lambda x: x.get("when_ist", ""), reverse=True)
    if not a.dry_run:
        save_state(state)

    doc = {"as_of": to_ist(now_utc_naive()).strftime("%Y-%m-%d %H:%M IST"), "tickers": syms,
           "moves": moves, "items": fresh, "errors": errors}
    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        for sym in syms:
            mv = moves.get(sym)
            if mv:
                tag = "  MOVING" if abs(mv["move_pct"]) >= a.move_pct else ""
                print("%-10s ₹%.2f %+.2f%% vs prev close ₹%.2f%s" % (sym, mv["price"], mv["move_pct"], mv["prev_close"], tag))
            else:
                print("%-10s no price" % sym)
        if not fresh:
            print("\nNothing new in the last %.0f hours." % a.hours)
        for it in fresh:
            bang = "!" if "risk" in it["tags"] or "halt" in it["tags"] or "regulatory" in it["tags"] else ""
            print("\n[%s]%s %s  (%s, %s)" % (it["when_ist"], bang, it["text"][:160], it["symbol"], "/".join(it["tags"])))
            if it.get("link"):
                print("  %s" % it["link"])
            elif it.get("attachment"):
                print("  %s" % it["attachment"])
        if errors:
            print("\n%d feed error(s), shown once: %s" % (len(errors), "; ".join(errors[:3])))
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())
