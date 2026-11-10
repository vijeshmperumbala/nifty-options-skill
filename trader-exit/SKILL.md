---
name: trader-exit
description: >-
  Exit Planner for Indian index options. Sets the stop and the target before
  you enter, on the INDEX where the setup actually fails, then translates both
  into the PREMIUM you will really lose or make, using the option's own greeks
  and a per-bar repricing model. Reads the entry from trader-entry (or a price
  you give it), places the stop at the opening-range midpoint by default (or
  the range low, a premium percentage, index points, or an ATR multiple), sets
  the target in R of the premium, adds the real Indian charge stack (STT,
  exchange fees, GST, stamp duty) to the reward-to-risk, and sets a flat-by
  time. Refuses a stop that values the option at zero, a target below the
  entry, a stop the charges would swallow, or a plan that is not worth its
  costs. Use when the user says "where is my stop", "set my stop and target",
  "plan the exit", "when do I get out", "stop loss", "what is my R", "risk
  reward", or right after /trader-entry triggers, before /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|premium-pct|index-points|atr] [--target 1.5R]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. In India this needs
one step the US version does not: **translation**.

You do not lose index points, you lose rupees. If NIFTY falls 60 points on a
long call with a 0.42 delta, the premium does not fall 60 - it falls about 25,
less if there is time left, more if implied vol drops at the same time. So the
plan is made in both units and written down in both: the index level is where
the setup failed, the premium is where the money stops.

Two exits are set, always, because an option can lose on a correct call:

- a **premium stop** (the money stop), and
- a **time stop**: flat by 15:15 IST, before the 15:30 close and before the
  brokers' own auto-square-off window.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                       # from trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1.5R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop premium-pct --premium-pct 30
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop index-points --points 45
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr --atr-multiple 1.2
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --underlying NIFTY --right PE \
    --premium 246.08 --spot 25044.65 --entry 24845.75 --setup orb
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                       # the current plan
```

Flags: `--stop`, `--premium-pct`, `--points`, `--atr-multiple`, `--target`
(`1.5R`, `2R`, `premium:350`, `index:24650`), `--flat-by HH:MM`, `--lots` (for
the rupee maths), `--chain`, `--chain-file`, `--write`, `--json`, `--now`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | symbol, strike, right, expiry, lot, trigger price, opening range, greeks |
| option chain | to reprice the option at the stop and at the target |
| `trader/config.json` | `stop_rule`, `stop_premium_pct`, `target_r`, `min_reward_risk`, `flat_by`, `risk_free_rate`, `dividend_yield`, `charges` |
| `trader/plan.json` | the plan: entry, stop and target in BOTH index and premium, R, charges, flat-by |

`/trader-timemachine`, `/trader-budget`, `/trader-risk` and `/trader-alerts`
all read `plan.json`. It is the contract between them.

## The stop rules

| rule | the stop | when it is right |
| --- | --- | --- |
| `or-mid` (default) | the midpoint of the opening range | the setup is a range break; the midpoint is where the break has failed |
| `or-low` | the range low for a call, the range high for a put | you want the break to stay broken |
| `premium-pct` | entry premium minus N% (default 30%) | the index level is noisy but you know what you paid |
| `index-points` | entry minus/plus N index points | you have a level in mind from the chart |
| `atr` | entry minus/plus `--atr-multiple` x daily ATR | the index's own volatility sets the distance |

The target is set in **R of the premium**, not in index points: `1.5R` means
the premium has to gain 1.5 times what it risks. Then the tool solves back for
the index level that produces that premium, so you can watch either one.

## What it refuses, and why

- A stop that values the option at Rs 0.05 or less. If the index has to move
  that far, the option is not stopped out - it is wiped out. Use a premium stop.
- A stop at or above the entry premium, or a target at or below it.
- A plan whose risk per unit is smaller than the round-trip charges per unit.
  On a NIFTY lot the round trip is roughly Rs 45-100; a Rs 4 stop is a donation.
- A reward-to-risk after charges below `min_reward_risk` (default 1.2).
- A direction that contradicts itself (call with the stop above the entry).

## Steps

1. Run it right after `/trader-entry` triggers, and before anything is bought.
2. Tell the user the four numbers that matter: the index stop, the premium
   stop, the premium target, and the R after charges.
3. Say the flat-by time out loud. An option carried to 15:29 on its expiry day
   is not a plan, it is a coin toss with the settlement VWAP.
4. If it refuses, say why in one line and fix the rule, not the number.
5. Hand off to `/trader-timemachine` before `/trader-budget`.

## Example: Monday 9 November 2026, NIFTY 24,850 PE

Entry at 10:45 on the 10:40 close of 24,845.75; ATM put priced at Rs 246.08;
opening range 24,899.40 to 25,081.00.

```
Plan - NIFTY26N1724850PE  (long put, weekly expiry 2026-11-17, lot 65)
  entry   index 24,845.75 -> premium Rs 246.08 (MODELLED - not a quote)
  stop    index 24,990.20 (or-mid) -> premium Rs 178.34   risk Rs 67.74 a unit
  target  premium Rs 347.69 -> index 24,648.95   reward Rs 101.61 a unit
  one lot (65 units): risk Rs 4,480, reward Rs 6,507, charges Rs 98 round trip, R:R 1.453
  premium outlay Rs 15,995; flat by 15:15 IST
```

Two things to notice. The stop is 145 index points away, which sounds generous
until you see it is only Rs 68 of premium - the put's delta is 0.48 and it also
buys back some time value as the index falls. And 1.5R is Rs 101 a unit, a
whole 41% gain in the premium, for a move of about 197 index points. That is
what an 8-day option asks for.

## Rules

- Every number comes from the plan, the chain and the config. Never hand-adjust
  a stop mid-trade; write a new plan and say so.
- A stop is a plan, not a guarantee. A gap or an IV crush can fill far worse;
  a limit-sl order can be skipped entirely in a fast market.
- Long premium only. Selling premium has unlimited risk and different margin
  rules (the 2% expiry-day ELM alone is a separate conversation); it is off
  unless the config says otherwise.
- On expiry day, the plan must say so, and the time stop dominates everything.

## What it never does

It never places an order, never sizes a position, never moves a stop to make a
plan look better, never promises a fill, and never plans a trade it has not
priced.

## Safety

- Education and tooling, not investment advice. A plan is arithmetic, not a prediction.
- Paper trading by default.
- Nothing places an order except `/trader-broker`, after `/trader-risk` passes that exact order and the
  user says yes to it.
- No options selling, no leverage on the premium (SEBI requires 100% upfront since 1 Feb 2025).
