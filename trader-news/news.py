#!/usr/bin/env python3
"""News Monitor - what is moving the Indian market today, and what could break a trade.

    python3 news.py                        # market-wide, last 12 hours
    python3 news.py --underlying NIFTY --hours 6
    python3 news.py --query "RBI policy"
    python3 news.py --events               # only the scheduled-risk list

Headlines come from Google News RSS (free, no key). A headline is what someone
wrote, not a direction: the tool labels the risk it implies (a policy decision,
an inflation print, an expiry) and stops there. It never tells you to trade the
news, and a day trade closed before 15:30 is the only kind that dodges it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import cli, clock, feed, instruments, store  # noqa: E402

RSS = ("https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en")
USER_AGENT = "Mozilla/5.0"

RISK_WORDS = {
    "rbi": "monetary policy",
    "repo rate": "monetary policy",
    "mpc": "monetary policy",
    "inflation": "macro data",
    "cpi": "macro data",
    "iip": "macro data",
    "gdp": "macro data",
    "fed": "global rates",
    "fomc": "global rates",
    "us cpi": "global rates",
    "tariff": "trade policy",
    "sebi": "regulation",
    "fii": "flows",
    "dii": "flows",
    "expiry": "expiry",
    "budget": "fiscal policy",
    "earnings": "single-stock risk",
    "results": "single-stock risk",
    "crude": "commodity",
    "rupee": "currency",
}

SCHEDULE_NOTES = [
    "RBI monetary policy meetings move the whole index in minutes; check the calendar before an intraday buy.",
    "CPI and IIP prints land at 17:30 IST, after the close - they change tomorrow's gap, not today's trade.",
    "The US Fed and US CPI land after Indian hours; the gap shows up at 09:15 the next session.",
    "NIFTY expires on Tuesday, SENSEX on Thursday: gamma and theta both accelerate on those mornings.",
]


def fetch(query, hours=12, limit=12):
    url = RSS.format(query=urllib.parse.quote(query))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=25) as response:          # noqa: S310
        payload = response.read()
    root = ET.fromstring(payload)
    cutoff = clock.now_ist() - dt.timedelta(hours=hours)
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        published = node.findtext("pubDate")
        source = node.findtext("source")
        when = None
        if published:
            try:
                when = email.utils.parsedate_to_datetime(published).astimezone(clock.IST)
            except (TypeError, ValueError):
                when = None
        if when and when < cutoff:
            continue
        items.append({
            "title": title,
            "link": link,
            "source": source or _source_from_title(title),
            "at": when.isoformat(timespec="seconds") if when else None,
            "age_minutes": int((clock.now_ist() - when).total_seconds() // 60) if when else None,
            "tags": _tags(title),
        })
    items.sort(key=lambda item: item["age_minutes"] if item["age_minutes"] is not None else 10 ** 9)
    return items[:limit]


def _source_from_title(title):
    if " - " in title:
        return title.rsplit(" - ", 1)[1]
    return ""


def _tags(text):
    lowered = text.lower()
    return sorted({label for word, label in RISK_WORDS.items() if word in lowered})


def market_snapshot(now):
    rows = []
    for name in ("NIFTY", "BANKNIFTY", "SENSEX"):
        try:
            quote = feed.quote(name)
            rows.append({"underlying": name, **{k: quote[k] for k in ("price", "change", "change_pct")}})
        except Exception as exc:                                   # noqa: BLE001
            rows.append({"underlying": name, "error": str(exc)})
    vix = None
    try:
        vix = feed.india_vix(now)
    except Exception:                                              # noqa: BLE001
        pass
    return {"indices": rows, "vix": vix}


def main(argv=None):
    ap = cli.parser("News Monitor - Indian market headlines and event risk")
    ap.add_argument("--underlying", help="focus: NIFTY, BANKNIFTY, SENSEX")
    ap.add_argument("--query", help="a custom search, e.g. 'RBI policy'")
    ap.add_argument("--hours", type=int, default=12)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--events", action="store_true", help="only the scheduled-risk list")
    args = ap.parse_args(argv)

    config, now = cli.context(args)
    snapshot = market_snapshot(now)

    if args.events:
        print("Scheduled risk that can invalidate a day trade:")
        for note in SCHEDULE_NOTES:
            print(f"  - {note}")
        return 0

    query = args.query
    if not query:
        focus = (args.underlying or config.get("underlying") or "NIFTY").upper()
        terms = ["Nifty", "Sensex"]
        if focus == "BANKNIFTY":
            terms = ["Bank Nifty", "Nifty", "Sensex"]
        query = " OR ".join(terms) + " when:1d"

    try:
        items = fetch(query, args.hours, args.limit)
    except Exception as exc:                                       # noqa: BLE001
        items = []
        print(f"! could not read Google News ({exc}). Try again, or check a terminal.", file=sys.stderr)

    payload = store.stamp({
        "query": query,
        "hours": args.hours,
        "market": snapshot,
        "headlines": items,
        "scheduled_risk": SCHEDULE_NOTES,
        "note": "headlines are reports, not signals; none of this is a direction",
    }, now)
    if not args.no_write:
        store.write_json("news.json", payload, config)

    if args.json:
        cli.emit(args, payload, "")
        return 0 if items else 1

    print(f"Market news - {clock.describe_session(now)}")
    for row in snapshot["indices"]:
        if "error" in row:
            print(f"  {row['underlying']:<10} no price ({row['error']})")
        else:
            print(f"  {row['underlying']:<10} {row['price']:>10,.2f}  {row['change_pct']:+.2f}%")
    if snapshot["vix"]:
        vix = snapshot["vix"]
        print(f"  India VIX  {vix['value']:>10,.2f}  {vix.get('change_pct', 0):+.2f}%  ({vix['regime']})")
    print()
    if not items:
        print("No headlines came back. That is not the same as no news: check a terminal before you trade.")
    else:
        print(f"{len(items)} headline(s) in the last {args.hours}h:")
        for item in items:
            age = f"{item['age_minutes']}m" if item["age_minutes"] is not None else "?"
            tags = f"  [{', '.join(item['tags'])}]" if item["tags"] else ""
            title = item["title"][:110]
            print(f"  {age:>5}  {title}{tags}")
            if item["source"]:
                print(f"         {item['source']}")
    print()
    print("Scheduled risk:")
    for note in SCHEDULE_NOTES:
        print(f"  - {note}")
    print()
    print("A day trade closed before 15:30 is not exposed to what lands after it. "
          "Nothing here is a reason to buy or sell.")
    if not args.no_write:
        print("Wrote trader/news.json")
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(cli.run(main))
