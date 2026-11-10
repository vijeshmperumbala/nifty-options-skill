"""Indian market structure: the rules the risk gate enforces, and why.

Sources checked 2026-11-09: SEBI's index-derivative measures of 1 Oct 2024 and
the follow-up circulars, the exchanges' contract specifications, and the
Finance Act 2026 (STT). Numbers live here so one file can be corrected when
SEBI moves again; the skills quote them instead of guessing.
"""

from __future__ import annotations

import datetime as dt

# --- contract structure ---------------------------------------------------
MIN_INDEX_CONTRACT_VALUE = 1_500_000      # rupees, SEBI band 15-20 lakh
LOT_MULTIPLE_RULE = ("Index options trade only in lots. 65 NIFTY units is the "
                     "smallest NIFTY position there is; size in whole lots.")

# --- margin and premium ---------------------------------------------------
UPFRONT_PREMIUM_FROM = dt.date(2025, 2, 1)   # buyers pay 100% of premium upfront
EXPIRY_DAY_ELM = 0.02                        # +2% extreme loss margin on short options expiring today
CALENDAR_SPREAD_BENEFIT_ON_EXPIRY = False

# --- expiry ---------------------------------------------------------------
WEEKLY_EXPIRY_INDEX = {"NSE": "NIFTY", "BSE": "SENSEX"}
WEEKLY_EXPIRY_DAY = {"NIFTY": "Tuesday", "SENSEX": "Thursday"}
EXPIRY_SETTLEMENT_VWAP_FROM = dt.date(2026, 12, 7)   # last 30-min VWAP, expiring contracts stop 15:30

# --- position limits ------------------------------------------------------
INTRADAY_NET_INDEX_LIMIT_CR = 5_000          # crore, per entity, index options
INTRADAY_GROSS_LIMIT_CR = 10_000
EOD_NET_INDEX_LIMIT_CR = 1_500
POSITION_SNAPSHOTS = 4                        # random per day, one between 14:45 and 15:30

# --- automation -----------------------------------------------------------
RETAIL_ALGO_FROM = dt.date(2026, 4, 1)        # registered app + static IP for API order placement

NOTES = {
    "upfront_premium": "Option buyers pay the full premium upfront; there is no intraday leverage on the premium.",
    "expiry_elm": "Short options expiring today carry an extra 2% extreme loss margin, and calendar-spread margin benefit is removed on expiry day.",
    "settlement": "Index options are European and cash-settled: no early exercise, and the settlement price is the expiry-day VWAP of the index.",
    "exercise_stt": "Letting an ITM option expire exercises it. STT is then charged on intrinsic value, not premium - usually worse than selling it before 15:30.",
    "algo": "From 1 Apr 2026 retail API order placement needs a registered algo app and a static IP; anything that fires orders automatically needs your broker's approval.",
    "weekly": "Each exchange runs a weekly expiry on one benchmark index only: NIFTY (Tuesday) on NSE, SENSEX (Thursday) on BSE. The other indices are monthly.",
}


def delta_notional(units: int, spot: float, delta: float) -> float:
    """Delta-adjusted exposure in rupees - what SEBI's position limits measure."""
    return abs(float(units) * float(spot) * float(delta))


def position_limit_usage(units: int, spot: float, delta: float) -> dict:
    notional = delta_notional(units, spot, delta)
    limit = INTRADAY_NET_INDEX_LIMIT_CR * 1e7
    return {"delta_notional": round(notional, 2), "limit": limit,
            "used_pct": round(100.0 * notional / limit, 6)}


def contract_value(spot: float, lot: int) -> float:
    return float(spot) * int(lot)


def below_min_contract_value(spot: float, lot: int) -> bool:
    return contract_value(spot, lot) < MIN_INDEX_CONTRACT_VALUE * 0.9


def short_expiry_margin_extra(units: int, spot: float) -> float:
    """The extra ELM a short option written on expiry day attracts, in rupees."""
    return EXPIRY_DAY_ELM * float(units) * float(spot)


def settlement_rule(expiry: dt.date) -> str:
    if expiry >= EXPIRY_SETTLEMENT_VWAP_FROM:
        return "expiry settles on the last 30 minutes' VWAP, contracts stop at 15:30"
    return "expiry settles on the exchange's published closing index value"


def structure_note(underlying: str, master=None, expiry=None, now=None) -> str:
    """A short, factual line about the contract being traded."""
    parts = [f"{underlying} deals in lots"]
    if master is not None and expiry is not None:
        try:
            lot = master.lot(underlying, expiry)
            parts.append(f"lot {lot}")
            parts.append(f"{master.series_kind(underlying, expiry)} series")
            parts.append(f"expiry {expiry.isoformat()} ({expiry.strftime('%A')})")
        except Exception:                                          # noqa: BLE001
            pass
    return ", ".join(parts) + "."
