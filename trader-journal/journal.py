#!/usr/bin/env python3
"""Trade Journal - your own record, and what it actually says.

    python3 journal.py stats                 # win rate, expectancy, R, charges
    python3 journal.py list --limit 20
    python3 journal.py add --symbol NIFTY26N1724850PE --entry 246 --exit 210 --qty 65 --risk 4480
    python3 journal.py stats --by setup

The paper fills from /trader-broker are written here automatically. Ten trades
tell you almost nothing; a hundred tell you something. The point of this skill
is to stop the story you tell yourself about your trading from being the only
record of it.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib")))

from indkit import cli, clock, store  # noqa: E402


def trades(config):
    """Pair entries with exits by symbol, in order."""
    rows = store.read_jsonl("journal.jsonl", config)
    open_legs = defaultdict(list)
    closed = []
    for row in rows:
        symbol = row.get("symbol")
        if row.get("event") == "entry":
            open_legs[symbol].append(row)
        elif row.get("event") == "exit":
            if open_legs[symbol]:
                entry = open_legs[symbol].pop(0)
                closed.append({"entry": entry, "exit": row})
            else:
                closed.append({"entry": None, "exit": row})
    return closed, sum(len(v) for v in open_legs.values())


def stats(closed, by="underlying"):
    if not closed:
        return None
    pnls, rs, charges = [], [], 0.0
    buckets = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "r": 0.0})
    streak = best_streak = 0
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for trade in closed:
        entry, exit_ = trade["entry"] or {}, trade["exit"]
        pnl = float(exit_.get("pnl") or 0.0)
        r = exit_.get("r_multiple")
        r = float(r) if r is not None else (pnl / float(entry.get("risk") or 1) if entry.get("risk") else 0.0)
        pnls.append(pnl)
        rs.append(r)
        charges += float(entry.get("charges") or 0) + float(exit_.get("charges") or 0)
        key = (entry.get(by) or exit_.get(by) or "?") if by else "all"
        bucket = buckets[key]
        bucket["n"] += 1
        bucket["wins"] += 1 if pnl > 0 else 0
        bucket["pnl"] += pnl
        bucket["r"] += r
        streak = streak + 1 if pnl > 0 else 0
        best_streak = max(best_streak, streak)
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    return {
        "trades": len(pnls),
        "wins": wins,
        "losses": losses,
        "win_rate": round(100.0 * wins / len(pnls), 1),
        "total_pnl": round(sum(pnls), 2),
        "avg_win": round(gross_win / wins, 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / losses, 2) if losses else 0.0,
        "avg_r": round(sum(rs) / len(rs), 3),
        "expectancy_r": round(sum(rs) / len(rs), 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "charges_paid": round(charges, 2),
        "max_drawdown": round(max_dd, 2),
        "best_win_streak": best_streak,
        "buckets": {str(k): {"trades": v["n"], "win_rate": round(100.0 * v["wins"] / v["n"], 1),
                             "pnl": round(v["pnl"], 2), "avg_r": round(v["r"] / v["n"], 3)}
                    for k, v in buckets.items()},
    }


def main(argv=None):
    ap = cli.parser("Trade Journal - your record, and what it says")
    ap.add_argument("command", nargs="?", default="stats", choices=["stats", "list", "add"])
    ap.add_argument("--by", default="underlying", choices=["underlying", "right", "symbol", "setup"])
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--symbol")
    ap.add_argument("--entry", type=float)
    ap.add_argument("--exit", type=float)
    ap.add_argument("--qty", type=int)
    ap.add_argument("--risk", type=float)
    ap.add_argument("--underlying")
    ap.add_argument("--setup", default="manual")
    ap.add_argument("--note")
    args = ap.parse_args(argv)

    config, now = cli.context(args)

    if args.command == "add":
        if not all([args.symbol, args.entry, args.exit, args.qty]):
            cli.fail("add needs --symbol, --entry, --exit and --qty (premiums and units)")
        pnl = (args.exit - args.entry) * args.qty
        risk = args.risk or abs(args.entry * 0.3 * args.qty)
        stamp = clock.now_ist(now).isoformat(timespec="seconds")
        store.append_jsonl("journal.jsonl", {
            "event": "entry", "at": stamp, "symbol": args.symbol, "underlying": args.underlying,
            "qty": args.qty, "entry_premium": args.entry, "risk": risk, "setup": args.setup,
            "manual": True,
        }, config)
        store.append_jsonl("journal.jsonl", {
            "event": "exit", "at": stamp, "symbol": args.symbol, "qty": args.qty,
            "exit_premium": args.exit, "pnl": round(pnl, 2),
            "r_multiple": round(pnl / risk, 3) if risk else None, "reason": args.note or "manual",
        }, config)
        print(f"Logged {args.symbol}: entry Rs {args.entry:.2f} -> exit Rs {args.exit:.2f}, "
              f"{args.qty} units, P&L Rs {pnl:,.0f}")
        return 0

    closed, still_open = trades(config)

    if args.command == "list":
        if not closed:
            cli.fail("no closed trades yet; the paper fills in /trader-broker are logged automatically")
        print(cli.table(["when", "symbol", "entry", "exit", "qty", "P&L", "R", "reason"],
                        [[str(t["exit"].get("at", ""))[:16], t["exit"].get("symbol", "")[:20],
                          _n((t["entry"] or {}).get("entry_premium")), _n(t["exit"].get("exit_premium")),
                          t["exit"].get("qty", ""), _n(t["exit"].get("pnl"), 0),
                          _n(t["exit"].get("r_multiple"), 2), t["exit"].get("reason", "")]
                         for t in closed[-args.limit:]],
                        aligns=["<", "<", ">", ">", ">", ">", ">", "<"]))
        return 0

    summary = stats(closed, args.by)
    if not summary:
        print("No closed trades yet. Entries and exits are logged by /trader-broker's paper fills, "
              "or by journal.py add.")
        return 1
    payload = store.stamp({"summary": summary, "open_positions": still_open}, now)
    if args.json:
        cli.emit(args, payload, "")
        return 0
    print(f"Journal - {summary['trades']} closed trade(s), {still_open} still open")
    print(f"  win rate {summary['win_rate']:.1f}%  ({summary['wins']}W / {summary['losses']}L)")
    print(f"  total P&L Rs {summary['total_pnl']:,.0f} before charges already netted per trade; "
          f"charges paid Rs {summary['charges_paid']:,.0f}")
    print(f"  average win Rs {summary['avg_win']:,.0f}, average loss Rs {summary['avg_loss']:,.0f}, "
          f"profit factor {summary['profit_factor']}")
    print(f"  expectancy {summary['expectancy_r']:+.3f}R a trade; max drawdown Rs {summary['max_drawdown']:,.0f}")
    print(f"\n  by {args.by}:")
    print(cli.table([args.by, "trades", "win%", "P&L", "avg R"],
                    [[k, v["trades"], f"{v['win_rate']:.1f}", f"{v['pnl']:,.0f}", f"{v['avg_r']:+.2f}"]
                     for k, v in sorted(summary["buckets"].items())],
                    aligns=["<", ">", ">", ">", ">"]))
    if summary["trades"] < 30:
        print("\n  Fewer than 30 trades: this is a sample, not a track record.")
    return 0


def _n(value, digits=2):
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "-"


if __name__ == "__main__":
    sys.exit(cli.run(main))
