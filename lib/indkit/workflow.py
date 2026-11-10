"""The steps every skill shares: pick the contract, read the opening range,
translate an index stop into a premium stop, and talk about it in plain words.

Keeping this here is what makes the pack coherent: the scanner, the entry
timer, the exit planner, the Time Machine and the risk gate all describe the
same trade the same way, and all of them label a modelled premium as modelled.
"""

from __future__ import annotations

import datetime as dt

from . import bs, chain as chain_mod, clock, feed, instruments, costs


# --------------------------------------------------------------- context
def day_context(now, config):
    """Session state plus the two things that most often invalidate a plan."""
    moment = clock.now_ist(now)
    phase = clock.phase(moment, config)
    phase["vix"] = None
    phase["vix_error"] = None
    try:
        phase["vix"] = feed.india_vix(moment)
    except Exception as exc:                                       # noqa: BLE001
        phase["vix_error"] = str(exc)
    phase["expiry_note"] = None
    return phase


def tradable_expiry(master, underlying, now=None, config=None):
    """The expiry a NEW position should use.

    Today's contract is skipped once the session is over, and skipped entirely
    when expiry-day entries are switched off - which is the default, because
    buying an option on its expiry morning is a different (much faster) game.
    """
    config = config or {}
    moment = clock.now_ist(now)
    today = moment.date()
    expiry = master.nearest_expiry(underlying, today)
    if expiry is None:
        return None
    allow_today = bool(config.get("allow_expiry_day_entries", False))
    if expiry == today and (moment.time() >= clock.SESSION_END or not allow_today):
        expiry = master.nearest_expiry(underlying, today, min_days=1)
    return expiry


def spot_for(underlying, now=None):
    symbol = instruments.UNDERLYINGS.get(underlying.upper(), {}).get("spot")
    if not symbol:
        raise RuntimeError(f"no free index feed for {underlying}; pass --spot")
    return feed.quote(symbol, )["price"]


# --------------------------------------------------------------- contracts
def pick_contract(underlying, spot, config=None, now=None, direction="CE",
                  strike_mode=None, offset=None, chain_source="auto", chain_path=None,
                  expiry=None):
    """Choose the option to trade and get its premium.

    Returns a dict that always carries `premium_source`, so no skill can
    accidentally present a modelled premium as a live quote.
    """
    config = config or {}
    moment = clock.now_ist(now)
    underlying = underlying.upper()
    direction = direction.upper()
    master = instruments.Master.load(config, now=moment)
    expiry = expiry or tradable_expiry(master, underlying, moment, config)
    if expiry is None:
        raise RuntimeError(f"no tradable expiry for {underlying}")
    lot = master.lot(underlying, expiry)
    step = master.step(underlying, expiry)
    days = (expiry - moment.date()).days
    series = master.series_kind(underlying, expiry)
    exchange = instruments.UNDERLYINGS[underlying]["exchange"]

    chain = chain_mod.load(underlying, spot=spot, expiry=expiry, config=config, now=moment,
                           source=chain_source, path=chain_path, lot=lot)
    mode = (strike_mode or config.get("strike_selection", "atm")).lower()
    atm_iv = chain.atm_iv(moment)
    years = bs.years_to_expiry(moment, expiry)

    if expiry == moment.date():
        # on expiry day the model is close to useless; prefer the real chain
        pass

    if mode == "delta":
        target_delta = float(config.get("target_delta", 0.5))
        strike, delta = bs.strike_for_delta(spot, years, float(config.get("risk_free_rate", 0.065)),
                                            atm_iv, direction, target_delta,
                                            q=float(config.get("dividend_yield", 0.012)), step=step)
        strike = chain.nearest_strike(strike) if strike in chain.rows else master.snap_strike(underlying, expiry, strike)
    else:
        atm = chain.nearest_strike(spot)
        shift = int(offset or 0)
        strike = atm + shift * step * (1 if direction == "CE" else -1)
        strike = min(chain.strikes(), key=lambda k: abs(k - strike))

    premium = chain.premium(strike, direction)
    quote = chain.leg(strike, direction)
    if premium is None:
        premium = chain.reprice(spot, direction, strike, moment)
        premium_source = "model"
    else:
        premium_source = chain.source

    contract = master.get(underlying, expiry, strike, direction)
    greeks = bs.greeks(spot, strike, years, float(config.get("risk_free_rate", 0.065)),
                       atm_iv, direction, float(config.get("dividend_yield", 0.012)))
    spread = None
    if quote.get("bid") and quote.get("ask"):
        spread = round(100.0 * (float(quote["ask"]) - float(quote["bid"])) / max(premium or 1e-9, 1e-9), 2)

    return {
        "underlying": underlying,
        "name": instruments.UNDERLYINGS[underlying]["name"],
        "exchange": exchange,
        "order_exchange": "NFO" if exchange == "NSE" else "BFO",
        "spot": round(float(spot), 2),
        "expiry": expiry,
        "series": series,
        "days_to_expiry": days,
        "lot": lot,
        "strike_step": step,
        "strike": strike,
        "right": direction,
        "symbol": contract.symbol if contract else f"{underlying}{expiry:%d%b%y}{int(strike)}{direction}".upper(),
        "premium": round(float(premium), 2) if premium is not None else None,
        "premium_source": premium_source,
        "iv": round(atm_iv, 4),
        "delta": round(greeks["delta"], 4),
        "gamma": round(greeks["gamma"], 6),
        "theta": round(greeks["theta"], 4),
        "vega": round(greeks["vega"], 4),
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "spread_pct": spread,
        "oi": quote.get("oi"),
        "volume": quote.get("volume"),
        "chain_as_of": chain.as_of,
    }


# ------------------------------------------------------------ opening range
def orb_signal(bars, or_minutes=15, interval="5m", cutoff=None, now=None):
    """Did the session break its opening range, and when?

    The rule is the one the US pack used, moved to Indian hours: the first
    `or_minutes` of the 09:15 session is the range (three 5-minute bars by
    default), and the signal is the first COMPLETED bar that CLOSES beyond it.
    """
    rng = feed.opening_range(bars, or_minutes, interval)
    if not rng:
        return {"status": "no_data", "reason": "no bars in the opening range yet", "range": None}
    or_end = clock.opening_range_end(or_minutes)
    after = [b for b in bars if b.time.time() >= or_end]
    if cutoff:
        after = [b for b in after if (b.time + dt.timedelta(minutes=clock.INTERVALS[interval])).time() <= cutoff]
    best = None
    for bar in after:
        if bar.close > rng["high"]:
            best = {"status": "triggered", "direction": "CE", "level": rng["high"], "price": bar.close,
                    "at": bar.time, "why": "closed above the range high"}
            break
        if bar.close < rng["low"]:
            best = {"status": "triggered", "direction": "PE", "level": rng["low"], "price": bar.close,
                    "at": bar.time, "why": "closed below the range low"}
            break
    if best:
        best["range"] = rng
        best["bars_after"] = len(after)
        return best
    if not after:
        return {"status": "range-forming", "reason": f"the {or_minutes}-minute range is still forming",
                "range": rng}
    last = after[-1]
    distance_up = round(rng["high"] - last.close, 2)
    distance_down = round(last.close - rng["low"], 2)
    return {"status": "waiting", "range": rng, "last_close": last.close, "at": last.time,
            "to_high": distance_up, "to_low": distance_down,
            "reason": f"inside the range ({rng['low']:,.2f} to {rng['high']:,.2f})"}


# ------------------------------------------------------- premium translation
def premium_at_index(contract_info, index_level, config=None, now=None):
    """What the option is worth if the index sits at `index_level` now."""
    unchanged = (index_level == contract_info.get("spot"))
    if contract_info.get("premium_source") != "model" and unchanged and contract_info.get("premium") is not None:
        return contract_info["premium"]
    years = bs.years_to_expiry(clock.now_ist(now), contract_info["expiry"])
    return round(bs.price(index_level, contract_info["strike"], years,
                          float((config or {}).get("risk_free_rate", 0.065)),
                          contract_info["iv"], contract_info["right"],
                          float((config or {}).get("dividend_yield", 0.012))), 2)


def index_for_premium(contract_info, target_premium, config=None, now=None):
    """The index level that would put the premium at `target_premium`.

    Solved numerically on the Black-Scholes value, so it stays correct when
    gamma is large (expiry-day options) instead of trusting a delta estimate.
    """
    years = bs.years_to_expiry(clock.now_ist(now), contract_info["expiry"])
    rate = float((config or {}).get("risk_free_rate", 0.065))
    div = float((config or {}).get("dividend_yield", 0.012))
    right = contract_info["right"]
    target = float(target_premium)

    def value(level):
        return bs.price(level, contract_info["strike"], years, rate,
                        contract_info["iv"], right, div) - target

    low, high = contract_info["spot"] * 0.60, contract_info["spot"] * 1.40
    increasing = right == "CE"
    if increasing and (value(low) > 0 or value(high) < 0):
        return None
    if not increasing and (value(high) > 0 or value(low) < 0):
        return None
    for _ in range(100):
        mid = 0.5 * (low + high)
        diff = value(mid)
        if abs(diff) < 0.01:
            return round(mid, 2)
        if (diff < 0) == increasing:
            low = mid
        else:
            high = mid
    return round(0.5 * (low + high), 2)


def risk_math(entry_premium, stop_premium, target_premium, lots, lot_size,
              exchange="NSE", now=None, config=None):
    """Everything in rupees: risk, reward, charges, and R after charges."""
    rates = (config or {}).get("charges")
    trip_exit_stop = costs.round_trip(entry_premium, stop_premium, lots, lot_size, exchange,
                                      rates, on=clock.now_ist(now))
    trip_exit_target = costs.round_trip(entry_premium, target_premium, lots, lot_size, exchange,
                                        rates, on=clock.now_ist(now))
    units = int(lots) * int(lot_size)
    gross_risk = max(0.0, (entry_premium - stop_premium) * units)
    gross_reward = max(0.0, (target_premium - entry_premium) * units)
    net_risk = gross_risk + trip_exit_stop["total"]
    net_reward = max(0.0, gross_reward - trip_exit_target["total"])
    return {
        "units": units,
        "gross_risk": round(gross_risk, 2),
        "gross_reward": round(gross_reward, 2),
        "charges_round_trip": trip_exit_target["total"],
        "charges_at_stop": trip_exit_stop["total"],
        "net_risk": round(net_risk, 2),
        "net_reward": round(net_reward, 2),
        "reward_risk": round(net_reward / net_risk, 3) if net_risk else None,
        "risk_per_unit": round(entry_premium - stop_premium, 2),
        "reward_per_unit": round(target_premium - entry_premium, 2),
        "breakeven_move": trip_exit_target["breakeven_move"],
        "premium_outlay": round(entry_premium * units, 2),
    }


def describe_premium_source(info) -> str:
    source = info.get("premium_source")
    if source == "model":
        return ("premium MODELLED from the index level and India VIX - not a quote; "
                "check your terminal before you act on it")
    if source == "nse":
        return "premium from the NSE option chain"
    if source == "bse":
        return "premium from the BSE option chain"
    if source == "csv":
        return "premium from the chain file you imported"
    return f"premium source: {source}"


def refusal_reason(info, config) -> str:
    """Why this contract should not be traded at all, if it should not be."""
    config = config or {}
    premium = info.get("premium")
    if premium is None:
        return "no premium available for that strike"
    if premium < float(config.get("min_premium", 20)):
        return (f"premium Rs {premium:.2f} is under the Rs {config.get('min_premium', 20)} floor: "
                "illiquid strikes are a spread and a tick-size trap")
    if info.get("days_to_expiry") == 0 and not config.get("allow_expiry_day_entries", False):
        return "this contract expires today and expiry-day entries are switched off"
    spread = info.get("spread_pct")
    if spread is not None and spread > float(config.get("max_spread_pct", 1.5)) * 100:
        return f"the bid-ask spread is {spread:.1f}% of the premium - too wide to trade"
    return ""
