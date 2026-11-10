#!/usr/bin/env python3
"""Index Scanner - which NIFTY / BANK NIFTY / SENSEX charts match a rule today.

    python3 scan.py                       # daily setups, every configured index
    python3 scan.py daily
    python3 scan.py orb                   # opening-range breakouts so far
    python3 scan.py orb --at 10:15        # cut the clock at 10:15 IST
    python3 scan.py daily --underlying NIFTY --no-write

A hit means the chart matched a rule. It is not a recommendation, not a
prediction, and not an option to buy. The scanner writes trader/watchlist.json
for trader-news and trader-entry; it never sizes or places anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import cli, clock, feed, indicators, instruments, store, workflow  # noqa: E402


def daily_setups(underlying, config, now):
    """The pre-open scan: what the daily closes say about each index."""
    symbol = instruments.UNDERLYINGS[underlying]["spot"]
    rows = feed.daily_bars(symbol, 90)
    closes = [b.close for b in rows]
    if len(closes) < 25:
        return None, f"only {len(closes)} daily bars for {underlying}"
    last = closes[-1]
    prior_high = indicators.highest(closes[:-1], 20)
    prior_low = indicators.lowest(closes[:-1], 20)
    ema20 = indicators.ema(closes, 20)
    ema50 = indicators.ema(closes, 50)
    rsi = indicators.rsi(closes, 14)
    atr = indicators.atr(rows, 14)
    prev_close = closes[-2]
    flags = []
    if prior_high and last > prior_high:
        flags.append(("breakout_20d", "CE", f"close {last:,.2f} above the 20-session high {prior_high:,.2f}"))
    if prior_low and last < prior_low:
        flags.append(("breakdown_20d", "PE", f"close {last:,.2f} below the 20-session low {prior_low:,.2f}"))
    if ema20:
        if closes[-2] <= ema20 < last:
            flags.append(("reclaim_ema20", "CE", f"clawed back above the 20-day EMA {ema20:,.2f}"))
        if closes[-2] >= ema20 > last:
            flags.append(("lose_ema20", "PE", f"lost the 20-day EMA {ema20:,.2f}"))
    move5 = indicators.pct_change(closes, 5)
    move15 = indicators.pct_change(closes, 15)
    if move15 is not None and move5 is not None:
        if move15 > 3.0 and abs(move5) < 1.5:
            flags.append(("bull_flag", "CE", f"+{move15:.1f}% in 15 sessions, then {move5:+.1f}% in 5"))
        if move15 < -3.0 and abs(move5) < 1.5:
            flags.append(("bear_flag", "PE", f"{move15:.1f}% in 15 sessions, then {move5:+.1f}% in 5"))
    if rsi is not None and rsi < 30:
        flags.append(("oversold", "CE", f"RSI(14) {rsi:.1f}"))
    if rsi is not None and rsi > 70:
        flags.append(("overbought", "PE", f"RSI(14) {rsi:.1f}"))
    trend = None
    if ema20 and ema50:
        trend = "up" if ema20 > ema50 and last > ema20 else "down" if ema20 < ema50 and last < ema20 else "mixed"

    # today's gap, if the session has started
    gap = None
    if clock.market_state(now) in ("open", "post"):
        try:
            session = feed.session_bars(symbol, now=now)
            if session:
                gap = round(session[0].open - prev_close, 2)
        except Exception:                                          # noqa: BLE001
            gap = None

    return {
        "underlying": underlying,
        "name": instruments.UNDERLYINGS[underlying]["name"],
        "spot": round(last, 2),
        "change_pct": round(100.0 * (last - prev_close) / prev_close, 2),
        "ema20": round(ema20, 2) if ema20 else None,
        "ema50": round(ema50, 2) if ema50 else None,
        "rsi14": round(rsi, 1) if rsi else None,
        "atr14": round(atr, 1) if atr else None,
        "trend": trend,
        "gap": gap,
        "flags": [{"setup": name, "direction": direction, "detail": detail} for name, direction, detail in flags],
    }


def orb_scan(underlying, config, now, at=None, or_minutes=15, interval="5m"):
    """The intraday scan: has the index closed out of its opening range?"""
    symbol = instruments.UNDERLYINGS[underlying]["spot"]
    cutoff = None
    if at:
        cutoff = clock.now_ist(f"2000-01-01 {at}") if ":" in str(at) else None
        cutoff = cutoff.time() if cutoff else None
    bars = feed.session_bars(symbol, now=now, interval=interval)
    signal = workflow.orb_signal(bars, or_minutes, interval, cutoff=cutoff, now=now)
    master = instruments.Master.load(config, now=now)
    expiry = workflow.tradable_expiry(master, underlying, now, config)
    signal.update({
        "underlying": underlying,
        "name": instruments.UNDERLYINGS[underlying]["name"],
        "spot": round(bars[-1].close, 2) if bars else None,
        "bars": len(bars),
        "expiry": expiry,
        "series": master.series_kind(underlying, expiry) if expiry else None,
        "lot": master.lot(underlying, expiry) if expiry else None,
    })
    return signal



def main(argv=None):
    ap = cli.parser("Index Scanner - NIFTY / BANK NIFTY / SENSEX setups")
    ap.add_argument("mode", nargs="?", default="daily", choices=["daily", "orb"])
    ap.add_argument("--underlying", action="append", help="NIFTY, BANKNIFTY, SENSEX (repeatable)")
    ap.add_argument("--at", help="orb: cut the scan at this IST time (HH:MM)")
    ap.add_argument("--or-minutes", type=int, default=15, help="opening range length (default 15)")
    ap.add_argument("--interval", default="5m", choices=["1m", "5m"], help="bar size for orb")
    ap.add_argument("--min-hits", type=int, default=1)
    args = ap.parse_args(argv)

    config, now = cli.context(args)
    targets = args.underlying or config.get("underlyings") or ["NIFTY"]
    targets = [t.upper() for t in targets if t]
    if args.or_minutes:
        config["opening_range_minutes"] = args.or_minutes

    phase = clock.phase(now, config)
    try:
        vix = feed.india_vix(now)
    except Exception as exc:                                       # noqa: BLE001
        vix = {"value": None, "change_pct": None, "regime": "unavailable", "error": str(exc)}

    rows, hits, failures = [], [], []
    for underlying in targets:
        if underlying not in instruments.UNDERLYINGS:
            failures.append(f"{underlying}: not an index this pack knows")
            continue
        try:
            if args.mode == "daily":
                info = daily_setups(underlying, config, now)
                if info is None:
                    failures.append(f"{underlying}: no data")
                    continue
                rows.append(info)
                for flag in info["flags"]:
                    hits.append({
                        "underlying": underlying, "setup": flag["setup"], "direction": flag["direction"],
                        "detail": flag["detail"], "spot": info["spot"], "at": now.isoformat(timespec="seconds"),
                    })
            else:
                if not phase["is_trading_day"]:
                    failures.append(f"{underlying}: market closed ({phase['holiday'] or 'weekend'})")
                    continue
                info = orb_scan(underlying, config, now, args.at, args.or_minutes, args.interval)
                rows.append(info)
                if info.get("status") == "triggered":
                    hits.append({
                        "underlying": underlying, "setup": f"orb-{'up' if info['direction'] == 'CE' else 'down'}",
                        "direction": info["direction"], "detail": info["why"],
                        "level": info["level"], "price": info["price"],
                        "at": info["at"].isoformat(timespec="seconds"), "spot": info.get("spot"),
                    })
        except Exception as exc:                                    # noqa: BLE001
            failures.append(f"{underlying}: {exc}")

    payload = store.stamp({
        "mode": args.mode,
        "session": {k: str(v) for k, v in phase.items() if k in ("state", "entries_ok", "reason", "holiday")},
        "vix": vix,
        "scanned": len(targets),
        "index": rows,
        "hits": hits,
        "failures": failures,
        "note": "a hit means the chart matched a rule; it is not a recommendation",
    }, now)

    if not args.no_write:
        store.write_json("watchlist.json", payload, config)

    if args.json:
        cli.emit(args, payload, "")
        return 0 if len(hits) >= args.min_hits else 1

    print(f"Index scan ({args.mode}) - {clock.describe_session(now)}")
    if vix.get("value"):
        print(f"India VIX {vix['value']:.2f} ({vix.get('change_pct', 0):+.2f}%) - {vix['regime']} regime")
    if args.mode == "orb":
        print(cli.table(
            ["index", "spot", "range", "status", "when", "level", "bars"],
            [[r.get("underlying", ""), _num(r.get("spot")),
              (f"{_num(r['range']['high'])} / {_num(r['range']['low'])}" if r.get("range") else "-"),
              r.get("status", "-"),
              (r["at"].strftime("%H:%M") if r.get("at") else "-"),
              _num(r.get("level") or r.get("last_close")), r.get("bars", "-")] for r in rows],
            aligns=["<", ">", "<", "<", "^", ">", ">"]))
    else:
        print(cli.table(
            ["index", "spot", "chg%", "ema20", "ema50", "rsi14", "atr14", "trend"],
            [[r["underlying"], _num(r["spot"]), f"{r['change_pct']:+.2f}",
              _num(r["ema20"]), _num(r["ema50"]), _num(r["rsi14"]), _num(r["atr14"]), r["trend"] or "-"]
             for r in rows],
            aligns=["<", ">", ">", ">", ">", ">", ">", "<"]))

    print()
    if hits:
        print(f"{len(hits)} of {len(targets)} indices matched a rule:")
        for hit in hits:
            direction = "call side" if hit["direction"] == "CE" else "put side"
            print(f"  {hit['underlying']:<10} {hit['setup']:<14} ({direction})  {hit['detail']}")
    else:
        print("No index matched a rule. That is a valid answer: it means wait.")

    for failure in failures:
        print(f"  ! {failure}")
    print()
    if args.mode == "daily":
        print("Next: /trader-news, then /trader-entry on the index you pick. "
              "The scanner does not pick for you.")
    else:
        print("Next: /trader-entry (bar by bar) then /trader-exit (stop and target BEFORE you buy).")
    if not args.no_write:
        print("Wrote trader/watchlist.json")
    return 0 if len(hits) >= args.min_hits else 1


def _num(value):
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "-"


if __name__ == "__main__":
    sys.exit(cli.run(main))
