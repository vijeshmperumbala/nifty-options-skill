---
name: trader-analyst
description: >-
  Options Analyst for the Indian market. Reads the NIFTY, BANK NIFTY or SENSEX
  option chain and reports what the premiums say: ATM implied vol, the ATM
  straddle, the one-sigma expected move to expiry in points and percent, put-call
  ratio and open-interest walls (when the chain is real), max pain, skew, the
  strike ladder, the contract structure, and the India VIX regime with its
  percentile against the last 60 sessions. Also builds the expiry and holiday
  calendar from the exchange's own contract master. Reports, never recommends:
  it will not tell you a direction, and it refuses to invent open interest when
  the chain is modelled. Use when the user says "read the option chain", "what is
  PCR", "max pain", "expected move", "implied volatility", "India VIX", "how
  expensive are options", "when is expiry", "option chain analysis", or before
  choosing a strike in /trader-entry.
argument-hint: "[chain | vix | calendar] [--underlying NIFTY] [--chain-file PATH]"
---

# trader-analyst

The price of an option is the market's estimate of a range, and the chain is
where that estimate is written down. This skill reads it and translates:

- **The expected move** (ATM straddle, or one sigma from ATM implied vol) says
  how far the index can realistically travel by expiry. A target further away
  than the one-sigma move is a target the market is pricing as unlikely.
- **PCR and OI walls** say where option writers have concentrated their
  positions, which is where expiry tends to gravitate - on a REAL chain only.
- **India VIX** is the 30-day expected vol. It decides whether an option is
  cheap or rich in absolute terms, and it is the one number that tells you
  whether today is a day to buy premium at all.

It reports. It never concludes "buy calls".

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/analyst.py" chain                      # NIFTY
python3 "${CLAUDE_SKILL_DIR}/analyst.py" chain --underlying SENSEX
python3 "${CLAUDE_SKILL_DIR}/analyst.py" chain --expiry 2026-12-29
python3 "${CLAUDE_SKILL_DIR}/analyst.py" chain --chain-file my_chain.csv
python3 "${CLAUDE_SKILL_DIR}/analyst.py" vix
python3 "${CLAUDE_SKILL_DIR}/analyst.py" calendar
```

## What it reads and writes

| file | what |
| --- | --- |
| **Dhan option chain** (`DHAN_ACCESS_TOKEN` set) | live premiums, open interest, IV, greeks, bid/ask - the richest source, and the one that unlocks PCR, max pain and OI walls |
| NSE / BSE chain endpoint | live premiums, open interest, implied vols - when reachable |
| your CSV (`--chain-file`) | a chain exported from your broker terminal, the most reliable path |
| the model | Black-Scholes from the live index level and India VIX - labelled MODELLED |
| contract master | expiries, series (weekly/monthly), lots, strike steps |
| Yahoo `^INDIAVIX` | the VIX level and its 60-session range |
| `trader/analyst.json` | with `--write`, the report |

## What needs a real chain, and what does not

| number | needs | if modelled |
| --- | --- | --- |
| premiums, ATM IV, straddle, expected move, ladder | nothing extra | fine - it is a model price, and it says so |
| PCR, max pain, OI walls, skew | real open interest | refused, with the reason and the fix |

A Dhan chain needs no file at all: export `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN`
and every number below is real, including the open-interest structure. Without
them, the pack falls back to NSE/BSE (blocked on many networks), your CSV, then
the model - and it says which one it used every time.

An invented open-interest number would be worse than no number, so the tool
refuses rather than guesses. Either set the two Dhan environment variables, or
export a chain from your broker's terminal to CSV with columns `strike, ce_ltp, ce_oi, ce_iv, pe_ltp, pe_oi,
pe_iv` (names are matched loosely) and pass `--chain-file`.

## The India-specific structure it prints

- **Series.** NIFTY weekly expires Tuesday, SENSEX weekly Thursday, BANK NIFTY
  monthly on the last Tuesday. Which contracts exist comes from the master, not
  from a hardcoded assumption - holiday weeks shift them, and this tool shows
  the shifted date rather than the theoretical one.
- **Lot and strike step.** NIFTY 65 units, strikes every 50; SENSEX 20, strikes
  every 100; BANK NIFTY 30, every 100 (November 2026 master).
- **Cost of carry.** A model premium uses your `risk_free_rate` and
  `dividend_yield`; both are in config. Change them and the model changes - which
  is exactly why a real chain beats a model.
- **Settlement.** Index options are European and cash-settled. From 7 December
  2026 expiring contracts settle on the last 30 minutes' VWAP.

## Steps

1. `analyst.py chain --underlying NIFTY` before picking a strike.
2. Say the expected move out loud. If the plan's target is beyond it, say that
   the market is pricing that target as unlikely - it does not mean it cannot
   happen, it means you are buying a lottery-shaped payoff.
3. Check the VIX regime. Above 20, premiums are rich and theta is brutal for
   buyers; below 13, premiums are cheap and moves are small. Neither is a signal.
4. Say the source of every premium: live chain, your CSV, or MODELLED.
5. Never translate any of it into "so buy calls".

## Example

```
NIFTY chain - 2026-11-17 (weekly, 8d), spot 25,044.65, lot 65
  source: MODELLED - no open interest, no bid/ask
  ATM 25,050, IV 17.4%, one-sigma move to expiry +/- 640 points (2.55%)
  ATM straddle Rs 510.23 (call 266.70 + put 243.53), break-even move +/- 434 points

strike    call  call OI  call IV%     put  put OI  put IV%
24,850  382.35        -      17.5  159.47       -     17.5
25,000  293.23        -      17.4  220.14       -     17.4
25,050  266.70        -      17.4  243.53       -     17.4
25,150  218.48        -      17.4  295.18       -     17.4
```

Read it this way: with eight days left, the market prices a 640-point move
either way as one standard deviation. The straddle costs Rs 510, so a buyer of
both legs needs a 434-point move just to break even. That is the whole case for
and against buying premium in one number.

## Rules

- Every number comes from the chain, the master, or the VIX feed. Never estimate
  an open interest.
- Say the source. "Modelled" is not a dirty word; pretending it is a quote is.
- Never say a strike is cheap, expensive, a buy or a sell. Say what it costs and
  what the market implies.
- PCR is a positioning measure, not a predictor. Say that when quoting it.
- Free chain endpoints can lag by minutes and are unofficial: for anything that
  matters, check your terminal.

## What it never does

It never places an order, never recommends a direction, never publishes a
metric it does not have the data for, never calls a premium cheap or rich
without saying "relative to what", and never substitutes a model number for a
market one silently.

## Safety

- Education and tooling, not investment advice.
- Paper trading by default.
- Nothing places an order except `/trader-broker`, after `/trader-risk` passes that exact order and the
  user says yes to it.
