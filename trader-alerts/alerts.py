#!/usr/bin/env python3
"""Price alerts and the NSE clock for trader-alerts. Standard library only.

    python3 alerts.py clock                          # NSE open or closed, next open
    python3 alerts.py add --from-plan                # stop, target and flat-by-close from trader_in/plan.json
    python3 alerts.py add --opening-bell
    python3 alerts.py add --symbol RELIANCE --stop 1163.35
    python3 alerts.py add --symbol RELIANCE --target 1196.20
    python3 alerts.py add --at 15:15 --label "flat by close" --before-close
    python3 alerts.py add --symbol TCS --pct 3
    python3 alerts.py check                          # rings what hit, once; exit 1 if nothing rang
    python3 alerts.py quote RELIANCE --write          # -> trader_in/prices.json
    python3 alerts.py quote --holdings --write        # every ticker in your portfolio and plan
    python3 alerts.py list
    python3 alerts.py reset reliance-plan-stop

Rule types:
  above / target  the price is at or above "price"
  below / stop    the price is at or below "price"
  pct_move        the price moved "pct" percent or more from the previous close
  opening_bell    NSE opened today (rings once per trading day, 09:15 IST)
  time            the IST clock passed "at" (HH:MM) on a trading day
                   (rings once per trading day); "before_close" is kept for
                   compatibility with the US pack's rule shape but NSE's
                   regular calendar has no scheduled early closes, so it has
                   no effect here.

Quotes: Yahoo chart JSON only (query2.finance.yahoo.com/v8/finance/chart/<SYM>.NS,
falls back to query1 on a 429/5xx). Stooq, which the US pack also reads, has
no NSE coverage (it answered 404 for every NSE ticker tried), so it is not
included here -- Yahoo is the one, solid data source in this skill.

NSE calendar: the 2026 cash-equity holidays as published by third-party
aggregators (NSE's own circular page could not be fetched reliably in
testing); see trader-risk's SKILL.md for the full list and its caveats.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; trader-india-skill/1.0)"
PAUSE = 0.4
BACKOFF = 2.0
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DATA_DIR = "trader_in"

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


class AlertError(Exception):
    pass


# ---------------------------------------------------------------- time

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
        raise AlertError('--now wants a time like "2026-10-29 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def trading_day(d):
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    return (False, "NSE/BSE holiday: " + name) if name else (True, "")


def session(d):
    ok, _ = trading_day(d)
    return (OPEN, CLOSE) if ok else None


def market_status(now_utc):
    local = to_ist(now_utc)
    d = local.date()
    ok, reason = trading_day(d)
    st = {"now_ist": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(), "weekday": local.strftime("%A"),
          "calendar_known": d.year in CALENDAR_YEARS, "open": False, "reason": reason}
    s = session(d)
    if s:
        o, c = s
        st["opens"], st["closes"] = o.strftime("%H:%M"), c.strftime("%H:%M")
        if o <= local.time() < c:
            st["open"], st["reason"] = True, "regular session"
        elif local.time() < o:
            st["reason"] = "before the open"
        else:
            st["reason"] = "after the close"
    nd = d if (s and local.time() < s[0]) else d + datetime.timedelta(days=1)
    for _ in range(15):
        if session(nd):
            st["next_open"] = "%s 09:15" % nd.isoformat()
            break
        nd += datetime.timedelta(days=1)
    return st


def yahoo_symbol(sym):
    sym = sym.upper().strip()
    return sym if sym.endswith(".NS") or sym.endswith(".BO") else sym + ".NS"


def bare_symbol(sym):
    sym = sym.upper().strip()
    for suf in (".NS", ".BO"):
        if sym.endswith(suf):
            return sym[: -len(suf)]
    return sym


# ---------------------------------------------------------------- quotes

def yahoo_url(sym, host=YAHOO_HOSTS[0]):
    return "https://%s/v8/finance/chart/%s?range=1d&interval=1d" % (host, yahoo_symbol(sym))


def parse_yahoo(text, sym):
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise AlertError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise AlertError("Yahoo has no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    m = res[0].get("meta", {})
    price = m.get("regularMarketPrice")
    if price is None:
        raise AlertError("Yahoo has no price for %s" % sym)
    t = m.get("regularMarketTime")
    when = (datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else "")
    return {"symbol": bare_symbol(sym), "price": float(price), "high": m.get("regularMarketDayHigh"),
            "low": m.get("regularMarketDayLow"), "prev_close": m.get("previousClose") or m.get("chartPreviousClose"),
            "time": when, "currency": m.get("currency", "INR"), "source": "yahoo"}


_urlopen = urllib.request.urlopen
_sleep = time.sleep


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with _urlopen(req, timeout=timeout) as r:
            return 200, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except urllib.error.URLError as e:
        raise AlertError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def fetch_yahoo(sym, timeout=20):
    last = None
    for host in YAHOO_HOSTS:
        url = yahoo_url(sym, host)
        code, text = _get(url, timeout)
        if code == 429:
            _sleep(BACKOFF)
            code, text = _get(url, timeout)
        if code == 200:
            return text
        if code == 404 and text.strip().startswith("{"):
            return text
        last = (url, code)
        if code != 429 and code < 500:
            break
    raise AlertError("%s answered HTTP %d" % (last[0].split("?")[0], last[1]))


def get_quotes(symbols, pause=PAUSE):
    quotes, errors = {}, []
    for i, sym in enumerate(symbols):
        sym = bare_symbol(sym)
        try:
            if i and pause:
                _sleep(pause)
            text = fetch_yahoo(sym)
            quotes[sym] = parse_yahoo(text, sym)
        except AlertError as e:
            errors.append(str(e))
    return quotes, errors


def write_prices(path, quotes, now_utc):
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (ValueError, OSError):
            old = {}
    prices = dict(old.get("prices", {}))
    detail = dict(old.get("detail", {}))
    for sym, q in quotes.items():
        prices[sym] = q["price"]
        detail[sym] = {"as_of": q["time"] or now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": q["source"],
                        "prev_close": q.get("prev_close")}
    doc = {"as_of": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "prices": prices, "detail": detail}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    return doc


# ---------------------------------------------------------------- rules / state

def _write(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)


def _read(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return default


def rules_path():
    return os.path.join(DATA_DIR, "alerts.json")


def state_path():
    return os.path.join(DATA_DIR, "state", "alerts.json")


def load_rules():
    return _read(rules_path(), {"rules": {}})


def load_state():
    return _read(state_path(), {"rung": {}})


def validate(rule):
    t = rule.get("type")
    if t in ("above", "below", "target", "stop"):
        if "price" not in rule or "symbol" not in rule:
            raise AlertError("%s needs symbol and price" % t)
    elif t == "pct_move":
        if "symbol" not in rule or "pct" not in rule:
            raise AlertError("pct_move needs symbol and pct")
    elif t == "time":
        if not rule.get("at") or not HHMM.match(rule["at"]):
            raise AlertError('time needs "at" like "15:15"')
    elif t == "opening_bell":
        pass
    else:
        raise AlertError("unknown rule type %r" % t)


def active(rule, today):
    d = rule.get("date")
    return d is None or d == today


def ring_time(rule, status, now_ist_str):
    return now_ist_str


def evaluate(rules, quotes, status, state, now_ist_str):
    today = status["date"]
    fired = []
    for rid, rule in rules.items():
        if not active(rule, today):
            continue
        rung = state["rung"].get(rid)
        if rule["type"] in ("above", "target"):
            q = quotes.get(rule["symbol"])
            if not q:
                continue
            hit = q["price"] >= rule["price"]
            if hit and (not rung or (rule.get("rearm") and rung.get("off"))):
                fired.append((rid, rule, "%s at or above ₹%.2f (now ₹%.2f)" % (rule["symbol"], rule["price"], q["price"])))
                state["rung"][rid] = {"at": now_ist_str, "off": False}
            elif not hit and rung and rule.get("rearm"):
                rung["off"] = True
        elif rule["type"] in ("below", "stop"):
            q = quotes.get(rule["symbol"])
            if not q:
                continue
            hit = q["price"] <= rule["price"]
            if hit and (not rung or (rule.get("rearm") and rung.get("off"))):
                fired.append((rid, rule, "%s at or below ₹%.2f (now ₹%.2f)" % (rule["symbol"], rule["price"], q["price"])))
                state["rung"][rid] = {"at": now_ist_str, "off": False}
            elif not hit and rung and rule.get("rearm"):
                rung["off"] = True
        elif rule["type"] == "pct_move":
            q = quotes.get(rule["symbol"])
            if not q or not q.get("prev_close"):
                continue
            ref = rule.get("ref", q["prev_close"])
            move = (q["price"] / ref - 1.0) * 100.0
            direction = rule.get("direction")
            hit = abs(move) >= rule["pct"] and (direction is None or (direction == "up") == (move > 0))
            if hit and (not rung or rung.get("date") != today):
                fired.append((rid, rule, "%s moved %+.2f%% from ₹%.2f (now ₹%.2f)" % (rule["symbol"], move, ref, q["price"])))
                state["rung"][rid] = {"at": now_ist_str, "date": today}
        elif rule["type"] == "time":
            if not status["open"] and status["reason"] not in ("regular session", "after the close"):
                continue
            if status.get("opens", "") == "" or now_ist_str.split(" ")[1] < rule["at"]:
                continue
            if not rung or rung.get("date") != today:
                fired.append((rid, rule, "it is %s IST (set for %s)" % (now_ist_str.split(" ")[1], rule["at"])))
                state["rung"][rid] = {"at": now_ist_str, "date": today}
        elif rule["type"] == "opening_bell":
            if status["open"]:
                if not rung or rung.get("date") != today:
                    fired.append((rid, rule, "NSE opened today at %s IST" % status.get("opens", "09:15")))
                    state["rung"][rid] = {"at": now_ist_str, "date": today}
    return fired


def rules_from_plan(plan, cfg_flat_by=None):
    sym = bare_symbol(plan["symbol"])
    date = plan["date"]
    out = {}
    out["%s-plan-stop" % sym.lower()] = {"type": "stop", "symbol": sym, "price": plan["stop"], "date": date, "label": "plan stop"}
    out["%s-plan-target" % sym.lower()] = {"type": "target", "symbol": sym, "price": plan["target"], "date": date, "label": "plan target"}
    flat_by = plan.get("flat_by") or cfg_flat_by or "15:15"
    out["%s-flat-by" % sym.lower()] = {"type": "time", "at": flat_by, "date": date, "label": "flat by close",
                                        "text": ("Flat by close: if %s is still open, close it: trader-broker flatten "
                                                 "--symbol %s builds the sell ticket, then trader-risk and your yes." % (sym, sym))}
    return out


def holdings_symbols():
    syms = set()
    port = _read(os.path.join(DATA_DIR, "portfolio.json"), {})
    syms |= set((port.get("positions") or {}).keys())
    plan = _read(os.path.join(DATA_DIR, "plan.json"), {})
    if plan.get("symbol"):
        syms.add(bare_symbol(plan["symbol"]))
    return sorted(syms)


# ---------------------------------------------------------------- commands

def cmd_clock(a):
    st = market_status(parse_now(a.now))
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        if st["open"]:
            print("NSE OPEN (%s IST, %s)." % (st["now_ist"], st["reason"]))
        else:
            extra = " Next open: %s." % st["next_open"] if st.get("next_open") else ""
            print("NSE CLOSED (%s IST, %s).%s" % (st["now_ist"], st["reason"], extra))
        if not st["calendar_known"]:
            print("Note: the holiday calendar is only known for %s." % ", ".join(str(y) for y in CALENDAR_YEARS))
    return 0


def cmd_quote(a):
    now_utc = parse_now(a.now)
    syms = [bare_symbol(s) for s in a.symbols] if a.symbols else (holdings_symbols() if a.holdings else [])
    if not syms:
        print("No symbols: give one, or --holdings with a trader_in/portfolio.json or plan.json.")
        return 1
    quotes, errors = get_quotes(syms)
    if a.write:
        write_prices(os.path.join(DATA_DIR, "prices.json"), quotes, now_utc)
    if a.json:
        print(json.dumps({"quotes": quotes, "errors": errors}, indent=2))
    else:
        for sym, q in quotes.items():
            move = ""
            if q.get("prev_close"):
                move = " %+.2f%% vs prev close ₹%.2f" % ((q["price"] / q["prev_close"] - 1) * 100, q["prev_close"])
            print("%-10s ₹%.2f%s" % (sym, q["price"], move))
        for e in errors:
            print("error: %s" % e)
    return 0 if quotes else 1


def cmd_check(a):
    now_utc = parse_now(a.now)
    status = market_status(now_utc)
    rules = load_rules().get("rules", {})
    state = load_state()
    syms = sorted({r["symbol"] for r in rules.values() if "symbol" in r})
    quotes, errors = get_quotes(syms) if syms else ({}, [])
    fired = evaluate(rules, quotes, status, state, status["now_ist"])
    if not a.dry_run:
        _write(state_path(), state)
    if a.json:
        print(json.dumps({"status": status, "fired": [{"id": rid, "label": r.get("label", rid), "text": t} for rid, r, t in fired]}, indent=2))
    else:
        head = "NSE OPEN" if status["open"] else "NSE CLOSED"
        print("%s (%s IST, %s)." % (head, status["now_ist"], status["reason"]))
        if not fired:
            price_bits = ", ".join("%s ₹%.2f" % (s, q["price"]) for s, q in quotes.items())
            print("Nothing hit. %d rule(s) watched today%s." % (len(rules), (", " + price_bits) if price_bits else ""))
        for rid, r, t in fired:
            label = r.get("label", rid)
            extra = (" " + r["text"]) if r.get("text") else ""
            print("ALERT %s: %s.%s" % (label, t, extra))
    return 0 if fired else 1


def cmd_add(a):
    rules = load_rules()
    rules.setdefault("rules", {})
    now_utc = parse_now(a.now)
    added = []
    if a.from_plan:
        plan = _read(os.path.join(DATA_DIR, "plan.json"), None)
        if not plan:
            print("No trader_in/plan.json; run trader-exit first.")
            return 1
        cfg = _read(os.path.join(DATA_DIR, "config.json"), {})
        new = rules_from_plan(plan, cfg.get("flat_by"))
        rules["rules"].update(new)
        added = list(new.keys())
    elif a.opening_bell:
        rid = "opening-bell"
        rules["rules"][rid] = {"type": "opening_bell", "label": "opening bell"}
        added = [rid]
    else:
        if not a.symbol and not a.at:
            raise AlertError("give --symbol with --stop/--target/--pct, or --at for a time rule")
        if a.at:
            rid = "time-%s" % a.at.replace(":", "")
            rules["rules"][rid] = {"type": "time", "at": a.at, "label": a.label or ("time " + a.at),
                                    "before_close": a.before_close}
            added = [rid]
        else:
            sym = bare_symbol(a.symbol)
            if a.stop is not None:
                rid = "%s-stop-%s" % (sym.lower(), a.stop)
                rules["rules"][rid] = {"type": "stop", "symbol": sym, "price": a.stop, "label": "%s stop" % sym}
                added = [rid]
            elif a.target is not None:
                rid = "%s-target-%s" % (sym.lower(), a.target)
                rules["rules"][rid] = {"type": "target", "symbol": sym, "price": a.target, "label": "%s target" % sym}
                added = [rid]
            elif a.above is not None:
                rid = "%s-above-%s" % (sym.lower(), a.above)
                rules["rules"][rid] = {"type": "above", "symbol": sym, "price": a.above, "label": "%s above" % sym, "rearm": a.rearm}
                added = [rid]
            elif a.below is not None:
                rid = "%s-below-%s" % (sym.lower(), a.below)
                rules["rules"][rid] = {"type": "below", "symbol": sym, "price": a.below, "label": "%s below" % sym, "rearm": a.rearm}
                added = [rid]
            elif a.pct is not None:
                rid = "%s-pct-%s" % (sym.lower(), a.pct)
                rules["rules"][rid] = {"type": "pct_move", "symbol": sym, "pct": a.pct, "direction": a.direction, "label": "%s %.1f%% move" % (sym, a.pct)}
                added = [rid]
            else:
                raise AlertError("give one of --stop/--target/--above/--below/--pct with --symbol")
    _write(rules_path(), rules)
    for rid in added:
        r = rules["rules"][rid]
        print("Added %s: %s" % (rid, json.dumps(r)))
    return 0


def cmd_list(a):
    rules = load_rules().get("rules", {})
    if not rules:
        print("No rules yet.")
        return 1
    for rid, r in rules.items():
        print("%-28s %s" % (rid, json.dumps(r)))
    return 0


def cmd_reset(a):
    state = load_state()
    if a.rule_id in state.get("rung", {}):
        del state["rung"][a.rule_id]
        _write(state_path(), state)
        print("Reset %s." % a.rule_id)
        return 0
    print("No rung state for %s." % a.rule_id)
    return 1


def main(argv=None):
    p = argparse.ArgumentParser(description="NSE/BSE price alerts and clock")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("clock")
    pc.add_argument("--now")
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=cmd_clock)

    pq = sub.add_parser("quote")
    pq.add_argument("symbols", nargs="*")
    pq.add_argument("--holdings", action="store_true")
    pq.add_argument("--write", action="store_true")
    pq.add_argument("--now")
    pq.add_argument("--json", action="store_true")
    pq.set_defaults(func=cmd_quote)

    pk = sub.add_parser("check")
    pk.add_argument("--now")
    pk.add_argument("--dry-run", action="store_true")
    pk.add_argument("--json", action="store_true")
    pk.set_defaults(func=cmd_check)

    pa = sub.add_parser("add")
    pa.add_argument("--from-plan", action="store_true")
    pa.add_argument("--opening-bell", action="store_true")
    pa.add_argument("--symbol")
    pa.add_argument("--stop", type=float)
    pa.add_argument("--target", type=float)
    pa.add_argument("--above", type=float)
    pa.add_argument("--below", type=float)
    pa.add_argument("--pct", type=float)
    pa.add_argument("--direction", choices=["up", "down"])
    pa.add_argument("--rearm", action="store_true")
    pa.add_argument("--at")
    pa.add_argument("--label")
    pa.add_argument("--before-close", action="store_true")
    pa.add_argument("--now")
    pa.set_defaults(func=cmd_add)

    pl = sub.add_parser("list")
    pl.set_defaults(func=cmd_list)

    pr = sub.add_parser("reset")
    pr.add_argument("rule_id")
    pr.set_defaults(func=cmd_reset)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except AlertError as e:
        print("Error: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
