---
name: trader-scanner
description: >-
  Index Scanner for the Indian market. Scans NIFTY 50, BANK NIFTY and SENSEX
  for setups on daily closes (20-session breakout or breakdown, bull and bear
  flags, reclaim or loss of the 20-day EMA, RSI extremes, today's gap) and for
  intraday opening-range breakouts on 5-minute bars, then writes
  trader/watchlist.json for trader-news and trader-entry. Reads free Yahoo
  index data and the exchange's own contract master, so lots, expiries and
  strike steps are always current. Use when the user says "scan the market",
  "what is NIFTY doing", "find me setups", "opening range breakout", "is Nifty
  breaking out", "Bank Nifty setup", "Sensex setup", "build my watchlist",
  "what should I look at today", or at the start of the trading day before
  /trader-news.
argument-hint: "[daily | orb] [--underlying NIFTY] [--at HH:MM]"
---

# trader-scanner

The Index Scanner is the first step of an index-option trade. In India the
tradable instrument is the index option, but the *decision* still comes from
the index chart: NIFTY, BANK NIFTY and SENSEX are the three underlyings with
liquid options, and an opening-range break in the index is what a
call-or-put decision is actually made of.

This skill only lists charts that matched a rule. A scan is a list of charts,
not a list of trades, and a setup on a chart is not a prediction. Most retail
option buyers in India lose money; a scanner does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py"                    # daily setups, all three indices
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                # opening-range breakouts so far
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15     # cut the clock at 10:15 IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --underlying NIFTY
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --or-minutes 30
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --json
```

Flags: `--underlying` (repeatable), `--at HH:MM` (pin the orb cutoff, IST),
`--or-minutes N` (opening range length, default 15), `--interval 5m|1m`,
`--now "2026-11-09 10:15"` to replay a session, `--json`, `--no-write`,
`--min-hits N`. Exit code 0 when at least one rule matched, 1 when none did.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | written by every scan: `mode`, `as_of`, `index[]`, `hits[]`, `failures[]`, `vix` |
| `trader/config.json` | `underlyings`, `opening_range_minutes`, `trader_dir` |
| Yahoo Finance | `^NSEI`, `^NSEBANK`, `^BSESN` daily and 5-minute bars; `^INDIAVIX` |
| contract master | lots, expiries and strike steps (Kite's public dumps, cached 12h) |

`trader-news` and `trader-entry` read `hits[].underlying` and `hits[].direction`.

## The daily scan (before the open)

Computed from daily closes, so it can run any time the market is shut:

| setup | the rule | what it implies for options |
| --- | --- | --- |
| `breakout_20d` | last close above every close of the 20 sessions before it | call side |
| `breakdown_20d` | last close below every close of the 20 sessions before it | put side |
| `reclaim_ema20` | previous close under the 20-day EMA, last close above it | call side |
| `lose_ema20` | previous close above the 20-day EMA, last close under it | put side |
| `bull_flag` | +3% or more over 15 sessions, then inside 1.5% over the last 5 | call side |
| `bear_flag` | -3% or more over 15 sessions, then inside 1.5% over the last 5 | put side |
| `oversold` / `overbought` | RSI(14) under 30 / over 70 | contrarian, not a direction |

The table also prints spot, day change, EMA20, EMA50, RSI(14) and daily ATR -
the ATR is what the `/trader-exit` ATR stop uses.

## The opening-range scan (during the session)

The Indian session opens at 09:15 with a pre-open call auction from 09:00. The
opening range is the first `--or-minutes` (15) of continuous trading: the 09:15,
09:20 and 09:25 five-minute bars. A hit is the first COMPLETED 5-minute bar
that CLOSES above the range high (call side) or below the range low (put side),
at that bar's close time. A wick through the level is not a hit.

Then the scan checks the contract it would mean: expiry, lot, days to expiry,
and the ATM premium - so the user can see that "NIFTY up" costs Rs 16,000 a lot
before they commit to the idea.

Refusals are explicit: before 09:30, on a weekend or exchange holiday, after
the 15:30 close, or when the range is still forming, it prints one plain line
and writes nothing.

## Data and limits

- Yahoo's chart endpoint is free, unofficial and can lag. Bars are 5-minute
  bars, never ticks. A short User-Agent is required; a full browser UA gets 429.
- Index bars carry no volume, so no volume filter is applied to the index
  itself. Volume-based confirmation exists in `/trader-analyst` through option
  open interest, which is a real number.
- Partial results are said as partial. If `^NSEBANK` fails, the scan says so
  and scans the rest.
- NSE/BSE holidays are in `lib/indkit/clock.py` (2026 published, 2027 tentative).
  Expiry dates are never hardcoded: they come from the contract master, which
  already reflects holiday shifts.

## Steps

1. Before the open, run `scan.py daily`. Tell the user the counts and the top
   two or three hits per index.
2. After 09:30 (10:15 is the classic), run `scan.py orb`. Say how many indices
   broke their range and at what time, and that a later hit is closer to the
   price now.
3. The user picks ONE index. Hand it on: `/trader-news NIFTY` (what is moving
   it), then `/trader-entry NIFTY` (the entry bar and the option).
4. Never pick the index for them, and never call a hit a good trade.

## Example: Monday 9 November 2026 (real data, NIFTY)

Daily scan before the open:

```
index           spot   chg%      ema20      ema50  rsi14     atr14  trend
---------  ---------  -----  ---------  ---------  -----  --------  -----
NIFTY      25,044.65  +0.06  23,549.92  23,431.76  78.60    359.20  up
BANKNIFTY  59,741.25  +1.11  56,101.49  56,053.45  76.70    918.70  up
SENSEX     81,106.65  +0.10  76,012.08  75,375.80  79.80  1,146.30  up

NIFTY      breakout_20d   (call side)  close 25,044.65 above the 20-session high 25,030.35
NIFTY      overbought     (put side)   RSI(14) 78.6
```

Opening-range scan at 10:45 the same day: the range was 24,899.40 to 25,081.00,
and the first completed bar outside it was the 10:40 bar, closing at 24,845.75
-below the range low, so the put side. Note what the same morning shows: a
20-session breakout tagged the call side, and the first real break of the day
was downward. Both were true; only one was tradable at 10:40.

## Rules

- Say what was scanned: how many indices, how many bars came back, and the time
  of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the index has moved on. Re-run before acting on it.
- NIFTY's weekly expiry is Tuesday, SENSEX's is Thursday, and BANK NIFTY is
  monthly: say which contract series a hit would be traded in.

## What it never does

It never places, sizes or checks an order, never says which index to trade,
never picks a strike, never calls index data real-time, and never promises that
a setup works.

## Safety

- Education and tooling, not investment advice. A hit is a rules match, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's decision, in their own config.
- Nothing in this pack places an order except `/trader-broker`, and only when all three hold:
  `/trader-risk` passed that exact order today, the user said yes for that one order in the chat, and
  paper is off in their config.
- Options only by default (`allow_options: true`, `allow_short_premium: false`): buying calls and puts,
  never writing them.
- Broker keys and API tokens live in environment variables, never in the chat.
