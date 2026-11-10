---
name: trader-timemachine
description: >-
  Time Machine for Indian index options. Replays the rest of the session 10,000
  times from the index's own last 60 sessions of 5-minute bars, repricing the
  option at every bar with Black-Scholes so theta burns the premium and the
  index move is translated into rupees by the option's own greeks - then runs
  the same opening-range rule on every one of those past sessions. Prints the
  odds of target first, stop first or neither by the flat-by time, the premium
  distribution, the average R, the history and one honest verdict. Use when the
  user says "run the time machine", "replay this trade", "what are the odds",
  "simulate this option trade", "monte carlo", "backtest this setup", "how often
  does this hit the target", "is this a coin flip", or after /trader-exit
  writes a plan and before /trader-budget.
argument-hint: "[run] [path to plan.json] [--replays N] [--seed N]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. `/trader-exit` has set an
entry, a premium stop and a premium target. Before anything is sized, this asks
one question: if today plays out like the index's own last 60 sessions, how
often does this plan reach the target first, the stop first, or neither by the
time you would be flat?

It answers in premium, not in index points, which in India is the whole point.
An option is a decaying asset: the index can close exactly where you expected
and the trade can still lose, because eight days became seven and a half. The
repricing is what makes the odds honest.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                  # from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write          # -> trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --replays 4000 --seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:15
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --underlying NIFTY --right PE \
    --entry 246.08 --stop 178.34 --target 347.69 --spot 24845.75
```

Flags: `--replays N` (10,000), `--seed N` (19), `--window N` (2 bars either
side), `--until HH:MM`, `--now`, `--json`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | symbol, strike, right, expiry, entry/stop/target premiums, entry bar, R, flat-by |
| Yahoo 5-minute bars | 60 days of the index: the pool AND the history |
| `trader/timemachine.json` | with `--write`: the plan, the odds, the percentiles, the history, the verdict |

## How it works

1. **The pool.** Every FULL session before today: 75 five-minute bars, 09:15 to
   15:25. Half days, gaps and today are dropped. Each bar becomes four ratios
   to the bar before it (open, high, low, close), indexed by its slot in the day.
2. **One replay.** Start at the entry price. For each bar left in the session,
   draw a bar from the same time of day (plus or minus 2 slots) from a random
   past session, scale it to the current level, and **reprice the option** with
   Black-Scholes at that bar's clock time - so the time to expiry shrinks as the
   replay moves forward. Check the stop against the bar's adverse extreme, then
   the target against its favourable extreme. A bar that touches both counts as
   the stop.
3. **10,000 replays** give four numbers: target first, stop first, neither
   (still open at `--until`), and green (target first, or the premium above the
   entry at `--until`). Plus the 5th/50th/95th percentile premium and index level.
4. **The history.** The same opening-range rule is run on each of those past
   sessions, using **the plan's own premium-risk ratio** as the stop, so it is a
   like-for-like comparison instead of a coincidence of how wide that day's
   range happened to be. That is what really happened, replayed with the same
   pricing model - a model backtest, not a tick record.
5. **The verdict** compares the target-first share of the resolved replays with
   the break-even share, which at R is 1/(1+R): 50% at 1R, 40% at 1.5R, 33% at
   2R. Within 5 points of break-even is "close to break-even", 5-15 points
   "leans", 15+ "strongly".

**The seed** comes from `random.Random(seed)`, seed 19, so the same plan on the
same data gives the same numbers on any machine. Say the seed with the result.

**What it refuses:** fewer than 20 full past sessions, a flat-by time at or
before the entry bar, a plan with no premium stop. It prints one plain line and
stops rather than pretending.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`.
2. Run it with `--write`. Add `--until` if the plan's flat-by is not 15:15.
3. Tell the user, in this order: the four odds, the median and 90% range of the
   premium at the flat-by time, the index range, the average R, the history
   line, then the verdict exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. Do not
   dress it up.
5. If 40% or more of the replays hit neither, say that the premium at the
   flat-by time decides those, and that theta is doing that.
6. Say once that it is built from the last 60 sessions and is not a prediction.
7. If the user still wants the trade, hand off to `/trader-budget`.

## Example: the 24,850 put, 10:45 to 15:15

```
3,000 replays of the rest of the session, 10:45 to 15:15 IST (54 five-minute bars), seed 19.
  pool: 59 full past sessions of NIFTY, drawn bar by bar at the same time of day
  option repriced every bar: strike 24,850 PE, IV 17.4%, theta burning to 2026-11-17

  target first     5.9 %
  stop first      13.7 %
  neither         80.4 %   still open at 15:15
  green           52.7 %   target first, or above the entry premium at 15:15
  Premium at 15:15: median Rs 242.42, 90% between Rs 178.34 and Rs 347.69
  Average R: +0.000
History: the same rule fired on 59 of those past sessions: 13 target, 13 stop, 33 neither. Average R +0.185.
Leans toward the stop: target first 30.1% against a 40% break-even.
80.4% of the replays hit neither: the premium at 15:15 decides those.
```

The honest reading: on a 4.5-hour horizon this plan mostly does nothing. The
stop and the target are both wider than an afternoon of NIFTY, so four out of
five replays end with the premium roughly where it started - minus theta. That
is not a reason to take the trade; it is a reason to know what kind of trade it
is before sizing it. Also note that the history resolved far more often than
the simulation: real sessions trend, and a simulation with zero drift does not.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- Repeat the verdict line and the line under it as printed; do not rewrite them.
- The simulation draws bars; it does not model IV crush, a halt, or a headline.
  Say that once, plainly, next to the odds.
- Free Yahoo data is unofficial; bars are 5-minute bars, never ticks, never
  real-time.
- Always say the seed.
- One plan per run. A changed entry, stop, target or expiry is a new run.

## What it never does

It never places an order, never sizes a position, never changes the plan, never
calls a result a sure thing, and never prints a P&L or a profit promise. It
never mixes today's bars into the pool and never uses a bar that has not closed.

## Safety

- Education and tooling, not investment advice. Odds are not a forecast.
- Paper trading by default.
- Nothing places an order except `/trader-broker`, after `/trader-risk` passes that exact order and the
  user says yes to it.
- Option buying only by default; no selling, no margin, no leverage.
