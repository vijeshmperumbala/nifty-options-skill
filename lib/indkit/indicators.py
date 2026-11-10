"""The handful of indicators the scanner and the exit planner need.

All of them work on plain lists of closes (or bars) - no numpy, no pandas.
Every function returns None rather than a number when there is not enough
data, so a skill can say "not enough history" instead of printing a fantasy.
"""

from __future__ import annotations


def sma(values, period):
    if len(values) < period or period <= 0:
        return None
    window = values[-period:]
    return sum(window) / period


def ema(values, period):
    """Exponential moving average, seeded with the SMA of the first window."""
    if len(values) < period or period <= 0:
        return None
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    value = seed
    for price in values[period:]:
        value = price * k + value * (1 - k)
    return value


def rsi(values, period=14):
    """Wilder's RSI. Needs period+1 closes."""
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for index in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(bars, period=14):
    """Average true range over bars (use daily bars for a daily ATR)."""
    if len(bars) < 2:
        return None
    ranges = []
    for previous, current in zip(bars, bars[1:]):
        ranges.append(max(current.high - current.low,
                          abs(current.high - previous.close),
                          abs(current.low - previous.close)))
    window = ranges[-period:]
    return sum(window) / len(window) if window else None


def highest(values, period):
    return max(values[-period:]) if len(values) >= period else None


def lowest(values, period):
    return min(values[-period:]) if len(values) >= period else None


def pct_change(values, period):
    if len(values) < period + 1:
        return None
    then = values[-period - 1]
    if not then:
        return None
    return 100.0 * (values[-1] - then) / then


def percentile_rank(values, value):
    """Where `value` sits in `values`, 0-100 - used for the VIX regime."""
    if not values:
        return None
    below = sum(1 for v in values if v <= value)
    return round(100.0 * below / len(values), 1)
