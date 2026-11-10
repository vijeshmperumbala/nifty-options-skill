"""The trader/ folder: config, state and the JSON files each skill hands on.

Every skill reads what the last one wrote, so the files are the interface:

    trader/config.json       your limits, charges and windows (never overwritten)
    trader/watchlist.json    scanner -> news, entry
    trader/entry.json        entry    -> exit
    trader/plan.json         exit     -> timemachine, budget, alerts
    trader/timemachine.json  timemachine result
    trader/orders.json       budget   -> risk
    trader/state/risk.json   risk verdicts (fingerprinted per order)
    trader/state/broker.json what was placed today
    trader/journal.jsonl     every trade, for the journal skill
    trader/state/*.json      alerts, cache, instrument master
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from . import clock

DEFAULT_CONFIG = {
    # --- what you trade -------------------------------------------------
    "underlying": "NIFTY",
    "underlyings": ["NIFTY", "BANKNIFTY", "SENSEX"],
    "paper": True,

    # --- going live (all off by default; see trader-broker/SKILL.md) -----
    "live_broker": None,               # "dhan" once DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN are in the env
    "live_trading_enabled": False,     # second switch: paper=false alone still sends nothing
    "live_product": "CNC",             # Dhan productType for an option buy: CNC (delivery) or INTRADAY
    "max_entry_slippage_pct": 2.0,     # refuse a live entry when the real premium ran this far past the plan
    "order_poll_seconds": 1.0,
    "order_poll_tries": 10,

    # --- money ----------------------------------------------------------
    "capital": 500000,                 # rupees you are willing to trade with
    "max_loss_per_trade": 5000,        # rupees at the stop, including charges (1% of 5 lakh)
    "max_daily_loss": 15000,           # rupees of full-size risk in one day
    "max_trades_per_day": 2,
    "max_premium_pct_of_capital": 0.25,  # cap on premium outlay for one position
    "max_lots": 4,

    # --- the day --------------------------------------------------------
    "no_entries_before": "09:30",
    "no_entries_after": "14:30",
    "flat_by": "15:15",
    "opening_range_minutes": 15,
    "min_days_to_expiry": 1,           # 0 allows expiry-day trades
    "allow_expiry_day_entries": False,

    # --- the plan -------------------------------------------------------
    "stop_rule": "or-mid",             # or-mid | or-low | premium-pct | index-points | atr
    "stop_premium_pct": 30,            # used by stop_rule=premium-pct
    "target_r": 1.5,
    "min_reward_risk": 1.2,

    # --- instrument choice ---------------------------------------------
    "strike_selection": "atm",         # atm | delta
    "target_delta": 0.5,
    "min_premium": 20,                 # avoid illiquid sub-20 options
    "max_spread_pct": 1.5,             # bid-ask as % of premium, when a real chain exists

    # --- what is allowed ------------------------------------------------
    "allow_options": True,             # this pack is options-first
    "allow_short_premium": False,      # selling options = undefined-risk-ish; off by default
    "allow_futures": False,
    "require_stop": True,
    "blocked_underlyings": [],

    # --- regime guards --------------------------------------------------
    "max_vix": 28,                     # above this, no new entries at all
    "skip_expiry_day_after": "14:00",  # on expiry day, no new buys after this

    # --- market model ---------------------------------------------------
    "risk_free_rate": 0.065,
    "dividend_yield": 0.012,
    "instruments_max_age_hours": 12,
    "charges": {
        "brokerage_per_order": 20.0,
        "stt_sell_rate": 0.0015,
        "exchange_rate": {"NSE": 0.0003503, "BSE": 0.000325},
        "sebi_rate": 0.000001,
        "stamp_buy_rate": 0.00003,
        "gst_rate": 0.18,
    },
}

CONFIG_KEYS_NEVER_DEFAULTED = {"capital", "max_loss_per_trade"}


def trader_dir(config=None) -> str:
    path = (config or {}).get("trader_dir") or os.environ.get("TRADER_DIR") or "trader"
    return path if os.path.isabs(path) else os.path.join(os.getcwd(), path)


def load_config(path=None, trader_root=None):
    """Default config, then trader/config.json, then an explicit path."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))     # deep copy
    candidates = []
    if path:
        candidates.append(path)
    if trader_root:
        candidates.append(os.path.join(trader_root, "config.json"))
    candidates.append(os.path.join("trader", "config.json"))
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
                    config.update(json.load(handle))
            except (OSError, json.JSONDecodeError) as exc:
                raise SystemExit(f"config {candidate} is not readable JSON: {exc}") from None
            break
    config["trader_dir"] = trader_root or config.get("trader_dir") or "trader"
    return config


def path_for(name, config=None) -> str:
    root = trader_dir(config)
    return name if os.path.isabs(name) else os.path.join(root, name)


def read_json(name, config=None, default=None):
    full = path_for(name, config)
    if not os.path.exists(full):
        return default
    try:
        with open(full, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(name, payload, config=None) -> str:
    full = path_for(name, config)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    temporary = full + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.write("\n")
    os.replace(temporary, full)
    return full


def append_jsonl(name, payload, config=None) -> str:
    full = path_for(name, config)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")
    return full


def read_jsonl(name, config=None):
    full = path_for(name, config)
    if not os.path.exists(full):
        return []
    rows = []
    with open(full, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def stamp(payload, now=None) -> dict:
    payload = dict(payload)
    payload.setdefault("as_of", clock.now_ist(now).isoformat(timespec="seconds"))
    return payload


def fingerprint(order: dict) -> str:
    """A stable hash of an order, so a verdict only matches the exact order."""
    keys = ("symbol", "side", "qty", "order_type", "limit_price", "stop_price",
            "target_price", "product", "exchange")
    basis = json.dumps({k: order.get(k) for k in keys}, sort_keys=True, default=str)
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def today(now=None) -> str:
    return clock.now_ist(now).date().isoformat()


def ensure_example_config(destination) -> str:
    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump(DEFAULT_CONFIG, handle, indent=2)
        handle.write("\n")
    return destination
