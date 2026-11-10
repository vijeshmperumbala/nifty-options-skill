"""Shared command-line plumbing so each skill's tool stays readable.

Every tool in the pack accepts the same handful of flags: --now to pin the
(IST) clock, --config for a different config.json, --json for machine output,
--offline for saved data, --trader-dir for a different state folder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import clock, store


def skill_dir(script_file: str) -> str:
    return os.path.dirname(os.path.abspath(script_file))


def bootstrap(script_file: str) -> str:
    """Make ../lib importable when a skill runs from its own folder."""
    here = skill_dir(script_file)
    lib = os.path.abspath(os.path.join(here, "..", "lib"))
    if lib not in sys.path:
        sys.path.insert(0, lib)
    return here


def parser(description: str, with_now=True) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    if with_now:
        p.add_argument("--now", help="pin the clock, IST (e.g. '2026-11-09 10:15')")
    p.add_argument("--config", help="path to config.json (default trader/config.json)")
    p.add_argument("--trader-dir", help="state folder (default ./trader)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--offline", help="saved data file or folder to use instead of the network")
    p.add_argument("--no-write", action="store_true", help="print only, write no files")
    return p


def context(args):
    config = store.load_config(getattr(args, "config", None), getattr(args, "trader_dir", None))
    if getattr(args, "now", None):
        config["_now"] = args.now
    return config, clock.now_ist(getattr(args, "now", None))


def emit(args, payload, text):
    """Either JSON or human text, depending on --json."""
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str))
    elif text:
        print(text)


def run(main):
    """Run a tool's main(), turning expected data errors into one readable line."""
    from .chain import ChainError
    from .feed import FeedError
    from .instruments import MasterError
    try:
        return main()
    except (ChainError, FeedError, MasterError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def fail(message: str, code: int = 1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def rule(width=72, char="-"):
    return char * width


def money(value) -> str:
    try:
        return f"Rs {float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def points(value) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def table(headers, rows, aligns=None):
    """A tiny fixed-width table - no dependencies, readable in a terminal."""
    aligns = aligns or ["<"] * len(headers)
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    out = ["  ".join(f"{str(h):{aligns[i]}{widths[i]}}" for i, h in enumerate(headers))]
    out.append("  ".join("-" * w for w in widths))
    for row in rows:
        out.append("  ".join(f"{str(c):{aligns[i]}{widths[i]}}" for i, c in enumerate(row)))
    return "\n".join(out)
