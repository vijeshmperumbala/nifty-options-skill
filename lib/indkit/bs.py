"""Option maths for Indian index options: price, greeks, implied vol.

NSE/BSE index options are European and cash-settled, so a plain
Black-Scholes-Merton price with a dividend yield is the right model (no early
exercise, no borrow). India carries: the model takes a risk-free rate and an
index dividend yield, both configurable.

The point of this module in the pack is translation: a setup is decided on the
INDEX (a level, an opening range), but the money is in the PREMIUM. Delta turns
"the index must fall 60 points" into "the premium must fall about 18 rupees",
and the Time Machine reprices the option bar by bar as the index moves.
"""

from __future__ import annotations

import math

SQRT2 = math.sqrt(2.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1d2(S, K, T, r, sigma, q=0.0):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    vsqrt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vsqrt
    return d1, d1 - vsqrt


def price(S, K, T, r, sigma, right="CE", q=0.0) -> float:
    """Theoretical premium per unit. At/after expiry: intrinsic value."""
    right = right.upper()
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if right == "CE" else max(0.0, K - S)
    out = _d1d2(S, K, T, r, sigma, q)
    if out is None:
        return 0.0
    d1, d2 = out
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)
    if right == "CE":
        return S * disc_q * _norm_cdf(d1) - K * disc_r * _norm_cdf(d2)
    return K * disc_r * _norm_cdf(-d2) - S * disc_q * _norm_cdf(-d1)


def greeks(S, K, T, r, sigma, right="CE", q=0.0) -> dict:
    """delta, gamma, theta (per CALENDAR day, in premium points), vega (per 1 vol point)."""
    right = right.upper()
    if T <= 0 or sigma <= 0:
        intrinsic = max(0.0, S - K) if right == "CE" else max(0.0, K - S)
        delta = (1.0 if S > K else 0.0) if right == "CE" else (-1.0 if S < K else 0.0)
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "price": intrinsic}
    out = _d1d2(S, K, T, r, sigma, q)
    if out is None:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "price": 0.0}
    d1, d2 = out
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)
    pdf = _norm_pdf(d1)
    if right == "CE":
        delta = disc_q * _norm_cdf(d1)
        theta_year = (-S * disc_q * pdf * sigma / (2 * math.sqrt(T))
                      - r * K * disc_r * _norm_cdf(d2)
                      + q * S * disc_q * _norm_cdf(d1))
    else:
        delta = -disc_q * _norm_cdf(-d1)
        theta_year = (-S * disc_q * pdf * sigma / (2 * math.sqrt(T))
                      + r * K * disc_r * _norm_cdf(-d2)
                      - q * S * disc_q * _norm_cdf(-d1))
    return {
        "delta": delta,
        "gamma": disc_q * pdf / (S * sigma * math.sqrt(T)),
        "theta": theta_year / 365.0,
        "vega": S * disc_q * pdf * math.sqrt(T) / 100.0,
        "price": price(S, K, T, r, sigma, right, q),
    }


def implied_vol(target, S, K, T, r, right="CE", q=0.0, lo=1e-4, hi=5.0):
    """Solve for sigma. Returns None when the price is outside no-arbitrage bounds."""
    if target is None or target <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, (S - K) if right.upper() == "CE" else (K - S))
    if target < intrinsic - 1e-9:
        return None
    if price(S, K, T, r, hi, right, q) < target:
        return None          # the price implies a vol above the search ceiling
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        diff = price(S, K, T, r, mid, right, q) - target
        if abs(diff) < 1e-6:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-8:
            break
    return 0.5 * (lo + hi)


def years_to_expiry(now, expiry, session_end_hour=15, session_end_minute=30) -> float:
    """Calendar time to expiry, in years, measured to the 15:30 IST settlement.

    Index options stop trading at the close, so the last day still carries a
    few hours of time value; this keeps theta honest on expiry morning.
    """
    import datetime as dt
    from .clock import IST
    moment = now if isinstance(now, dt.datetime) else dt.datetime.combine(now, dt.time(9, 15))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    end = dt.datetime.combine(expiry, dt.time(session_end_hour, session_end_minute), IST)
    seconds = (end - moment.astimezone(IST)).total_seconds()
    return max(0.0, seconds / (365.0 * 24 * 3600))


def premium_for_index_move(premium0, S0, dS, delta, gamma=0.0) -> float:
    """Second-order estimate of the new premium after the index moves dS points."""
    return max(0.0, premium0 + delta * dS + 0.5 * gamma * dS * dS)


def index_move_for_premium(premium0, target_premium, delta, gamma=0.0):
    """The index move that takes the premium to target_premium (delta+gamma).

    Returns None when the move is out of reach (a premium cannot be pushed
    below zero by a favourable index move the option cannot deliver).
    """
    if target_premium < 0:
        return None
    if abs(delta) < 1e-9:
        return None
    # 0.5*g*dS^2 + delta*dS + (p0 - target) = 0
    a, b, c = 0.5 * gamma, delta, premium0 - target_premium
    if abs(a) < 1e-12:
        return -c / b
    disc = b * b - 4 * a * c
    if disc < 0:
        return None
    root1 = (-b + math.sqrt(disc)) / (2 * a)
    root2 = (-b - math.sqrt(disc)) / (2 * a)
    # take the root with the smaller absolute move consistent with the sign of delta
    candidates = [r for r in (root1, root2) if r * delta <= 0]
    if not candidates:
        return None
    return min(candidates, key=abs)


def strike_for_delta(S, T, r, sigma, right, target_delta=0.5, q=0.0, step=50.0):
    """Nearest practical strike whose |delta| is closest to target_delta.

    A buy-side trader picks moneyness by delta: ATM ~0.5, slightly OTM ~0.35,
    deep OTM ~0.2 or less. Returns (strike, delta_at_that_strike).
    """
    right = right.upper()
    best = None
    lo = S * 0.80
    hi = S * 1.20
    k = math.floor(lo / step) * step
    while k <= hi:
        g = greeks(S, k, T, r, sigma, right, q)
        diff = abs(abs(g["delta"]) - target_delta)
        if best is None or diff < best[0]:
            best = (diff, k, g["delta"])
        k += step
    return (best[1], best[2]) if best else (round(S / step) * step, 0.0)
