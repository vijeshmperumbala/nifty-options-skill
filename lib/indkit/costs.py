"""The Indian charge stack, in rupees.

Why this file exists: in index options the premium is small and the costs are
not. A NIFTY lot at a 120 premium is about 7,800 rupees of turnover, and a
round trip costs roughly 45-70 rupees all in - near 1% of the premium. A plan
that ignores costs overstates every reward-to-risk ratio, so every skill that
computes R uses this module.

Rates below are the published retail-discount-broker rates for NSE/BSE equity
options, checked 2026-11-09. They change (STT has moved twice in two years),
so they live in config.json and the contract note always wins.

    STT            0.15% of the SELL premium, from 1 Apr 2026 (0.10% before)
                   0.15% of intrinsic value if an ITM option is exercised
    Exchange       NSE ~0.03503% of premium turnover, both sides
                   BSE ~0.0325% of premium turnover, both sides
    SEBI fee       Rs 10 per crore of premium turnover (0.0001%)
    Stamp duty     0.003% of the BUY premium (0.002% for futures)
    GST            18% on (brokerage + exchange + SEBI), never on STT
    Brokerage      Rs 20 per executed order, flat (discount broker)
    IPFT           Rs 10 per crore, NSE only
"""

from __future__ import annotations

import datetime as dt

DEFAULTS = {
    "brokerage_per_order": 20.0,
    "stt_sell_rate": 0.0015,        # 0.15% of sell premium, from 2026-04-01
    "stt_sell_rate_before_apr2026": 0.0010,
    "stt_exercise_rate": 0.0015,
    "exchange_rate": {"NSE": 0.0003503, "BSE": 0.000325},
    "sebi_rate": 0.000001,          # Rs 10 per crore
    "stamp_buy_rate": 0.00003,      # 0.003% on the buy leg
    "gst_rate": 0.18,
    "ipft_rate": {"NSE": 0.000001, "BSE": 0.0},
    "dp_charge": 0.0,               # nothing for intraday F&O
}

STT_CHANGE_DATE = dt.date(2026, 4, 1)


def _merged(config=None) -> dict:
    """DEFAULTS, overlaid with a (possibly partial) config, nested dicts included."""
    out = {key: (dict(value) if isinstance(value, dict) else value) for key, value in DEFAULTS.items()}
    source = {}
    if isinstance(config, dict):
        if isinstance(config.get("charges"), dict):
            source.update(config["charges"])
        for key in DEFAULTS:
            if key in config and not isinstance(config[key], dict):
                source.setdefault(key, config[key])
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


def leg(premium, units, exchange="NSE", side="buy", rates=None, on=None) -> dict:
    """Charges on ONE leg. `units` is lots x lot size (the quantity traded)."""
    r = _merged(rates)
    exchange = exchange.upper()
    turnover = max(0.0, float(premium)) * max(0, int(units))
    brokerage = float(r["brokerage_per_order"])
    stt = 0.0
    if side.lower() == "sell":
        rate = r["stt_sell_rate"]
        if on is not None and _as_date(on) < STT_CHANGE_DATE:
            rate = r["stt_sell_rate_before_apr2026"]
        stt = turnover * rate
    exchange_fee = turnover * float(r["exchange_rate"].get(exchange, 0.0003503))
    sebi = turnover * float(r["sebi_rate"])
    ipft = turnover * float(r["ipft_rate"].get(exchange, 0.0))
    stamp = turnover * float(r["stamp_buy_rate"]) if side.lower() == "buy" else 0.0
    gst = (brokerage + exchange_fee + sebi + ipft) * float(r["gst_rate"])
    total = brokerage + stt + exchange_fee + sebi + ipft + stamp + gst
    return {
        "premium": round(float(premium), 4),
        "units": int(units),
        "turnover": round(turnover, 2),
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange": round(exchange_fee, 2),
        "sebi": round(sebi, 2),
        "ipft": round(ipft, 2),
        "stamp": round(stamp, 2),
        "gst": round(gst, 2),
        "total": round(total, 2),
    }


def round_trip(entry_premium, exit_premium, lots, lot_size, exchange="NSE",
               rates=None, on=None) -> dict:
    """Both legs, plus what the trade has to earn just to stand still."""
    units = int(lots) * int(lot_size)
    buy = leg(entry_premium, units, exchange, "buy", rates, on)
    sell = leg(exit_premium, units, exchange, "sell", rates, on)
    total = buy["total"] + sell["total"]
    return {
        "units": units,
        "buy": buy,
        "sell": sell,
        "total": round(total, 2),
        "per_unit": round(total / units, 3) if units else 0.0,
        "breakeven_move": round(total / units, 3) if units else 0.0,
        "pct_of_entry_premium": round(100.0 * total / (entry_premium * units), 2) if entry_premium and units else None,
    }


def exercise(settlement_price, strike, lots, lot_size, rates=None, on=None, exchange="NSE") -> dict:
    """Cost of letting an ITM option expire into cash settlement instead of selling it.

    STT is then charged on the intrinsic value, which for a deep ITM option is
    far bigger than the premium - one of the classic India-specific traps.
    """
    r = _merged(rates)
    units = int(lots) * int(lot_size)
    intrinsic = max(0.0, settlement_price - strike)
    stt = intrinsic * units * float(r["stt_exercise_rate"])
    return {
        "intrinsic": round(intrinsic, 2),
        "units": units,
        "stt_on_intrinsic": round(stt, 2),
        "note": "STT is charged on intrinsic value when an option is exercised, not on premium.",
    }


def _as_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return dt.date.today()


def summarise(trip: dict) -> str:
    """One plain line: what the round trip costs and what it has to earn."""
    return (f"charges Rs {trip['total']:.2f} for {trip['units']} units "
            f"(Rs {trip['per_unit']:.2f} a unit, {trip['pct_of_entry_premium']}% of the premium)")
