---
name: trader-news
description: >-
  News Monitor (India). Tracks breaking news on every stock you watch:
  NSE's own corporate-announcements feed (results, board meetings,
  disclosures) and Yahoo Finance news for `.NS` tickers as a thin fallback,
  plus how far each stock is from yesterday's close. Shows only what is new
  since the last check, newest first, in IST, tags each item (earnings,
  corporate-action, capital, people, deal, regulatory, analyst, halt, risk)
  and never repeats one. Use when the user says "any news on RELIANCE", "why
  is this moving", "what's the news on my watchlist", "morning briefing",
  "pre-market news", "monitor the news", "did anything break", "check the
  filings", or after /trader-scanner finds setups.
argument-hint: "[tickers]"
---

# trader-news (India)

A chart can look perfect and still be about to gap on a results date, a
board meeting or a regulatory notice. Before a setup becomes a trade, this
skill reads the two feeds that carry those first, for every stock on the
watchlist, and tells you only what is new. It also prints how far each stock
has moved today, so a headline and a move sit side by side.

There is no Indian equivalent of SEC EDGAR's structured XBRL filings that
this tool could read machine-to-machine; NSE's corporate-announcements feed
is the closest real substitute, and it is a PDF-attachment disclosure feed,
not structured financial data (see trader-analyst's note below for why the
earnings-analyst skill itself was not built for this pack).

`${CLAUDE_SKILL_DIR}` below is this skill's folder.

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                              # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE TCS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-nse --json
python3 "${CLAUDE_SKILL_DIR}/news.py" --all --dry-run
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader_in/watchlist.json` | the scanner's hits |
| `trader_in/plan.json` | the trade you are planning (trader-exit) |
| `trader_in/portfolio.json` | what you hold (trader-broker records fills there) |
| NSE corporate announcements | `nseindia.com/api/corporate-announcements?symbol=X` — official filings, primed with a session cookie first |
| Yahoo Finance search news | `query1.finance.yahoo.com/v1/finance/search?q=X` — kept only when the item's own `relatedTickers` names `X.NS` |
| `trader_in/state/news.json` | what has been shown (kept 30 days) |

## The two feeds, and which one to trust

- **NSE corporate announcements is the primary feed and the flaky one.** It
  is NSE's *live* `nseindia.com` API, the same host that is known to
  rate-limit or outright block scripted access, especially from outside
  India or from a data-center IP. This tool primes a session cookie against
  `nseindia.com/` first, which helped in testing, but a block is still
  possible on another network — it fails soft, per symbol, with the error
  shown once at the end, never crashing the whole run.
- **Yahoo Finance news is the fallback and it is thin.** There is no reliable
  per-ticker news endpoint for NSE symbols on Yahoo's free surface; a bare
  symbol search (`q=RELIANCE`) is a fuzzy text match against Yahoo's whole
  news corpus and mostly returns unrelated US stories (a search for
  "RELIANCE" mostly returns stories about Reliance Steel, a US company with
  the ticker RS). This tool keeps only the rare item whose own
  `relatedTickers` field actually names `<SYMBOL>.NS`, so expect far fewer
  hits than the US pack's Yahoo headline RSS, and treat an empty result as
  "nothing confirmed", not "nothing happened".
- **Price move is the solid feed.** Yahoo's chart meta for `.NS` tickers is
  the same reliable source trader-scanner and trader-alerts use.

## The rules

- **Announcements are filings, headlines are leads.** An NSE announcement is
  what the company told the exchange; read the linked PDF before repeating
  it, since the feed only gives the subject line. A Yahoo item is a lead:
  open the story before telling the user something happened.
- **Say what it touches.** Every item is tagged from its text: earnings,
  corporate-action, capital, people, deal, regulatory, analyst, halt, risk,
  or other. An item tagged risk, halt or regulatory is marked `!`.
- **MOVING** marks a stock 3% or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too.
- **No predictions.** Report what was filed or published and how far the
  price is from yesterday's close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the seen-state alone.
2. Read every `!` item, anything tagged earnings or corporate-action, and
   every MOVING ticker first.
3. Write a short brief, per ticker: what was filed or published, the source,
   the move, and one line on why it matters to the setup (a results date
   inside the trade window can gap through any stop).
4. Never act on a headline alone. Never trade because of a filing title you
   have not opened.

## What it never does

- Never invents or embellishes a filing, a story or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: a pause between calls per ticker, and a failed
  fetch for one symbol never stops the rest of the run.

## Why there is no earnings-analyst skill in this pack

The US pack's trader-analyst reads structured XBRL company facts straight
from SEC EDGAR — machine-readable revenue, margins and EPS, quarter by
quarter. India has no equivalent open, structured, machine-readable filing
API: NSE and BSE corporate filings are PDF attachments (financial results,
board outcomes), and MCA (the Registrar of Companies) data requires a paid
or manual lookup. Building a "reduced" analyst skill that just scrapes
numbers out of PDFs would be unreliable enough to be actively misleading in
a tool whose whole premise is "every number is real", so this pack leaves
that stage out rather than ship a weak version. Read the linked NSE result
PDF yourself when a results announcement comes through this skill.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing in this pack ever places a real order (see trader-broker);
paper/manual only, with no live broker integration of any kind.
