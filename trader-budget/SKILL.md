---
name: trader-budget
description: >-
  Budget Manager for Indian index options. Sizes a position in whole LOTS from
  your max loss per trade, the premium outlay, your capital and your max_lots
  cap - never from appetite - and shows the risk in rupees at the stop with STT,
  exchange charges, GST and stamp duty already counted. Then it writes the order
  set an Indian broker actually takes for a bought option: the entry, the
  stop-loss trigger, the target limit and the flat-by exit. Says plainly when
  not one lot fits, and what to change. Use when the user says "how many lots",
  "size this trade", "how much do I need", "position size", "how much will this
  cost", "afford", "quantity Nifty option", or after /trader-timemachine and
  before /trader-risk.
argument-hint: "[--lots N] [--write]"
---

# trader-budget

Indian index options are sized in whole lots, and a lot is big. As of November
2026 the exchange's own contract master says: **NIFTY 65 units, BANK NIFTY 30,
SENSEX 20**. One at-the-money NIFTY option at a premium of Rs 246 costs
Rs 15,995 of premium - paid in full, upfront, because SEBI requires 100% upfront
premium collection for buyers - and a sensible stop risks Rs 4,000-5,000 on that
one lot.

So this skill answers a narrower question than the US one: not "how many
shares", but **"does one lot even fit, and how many do before my max loss is
breached"**.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                # from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write        # -> trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --lots 3       # ask for 3 and see the verdict
```

Flags: `--lots N` (force a size and show whether it is allowed), `--write`,
`--json`, `--now`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | entry, stop and target premiums, lot, expiry, exchange |
| `trader/config.json` | `capital`, `max_loss_per_trade`, `max_premium_pct_of_capital`, `max_lots`, `max_daily_loss`, `charges` |
| `trader/orders.json` | the entry order plus three exit orders, and the plan they came from |

## The arithmetic, in order

1. **Loss for one lot at the stop**, charges included:
   `(entry premium - stop premium) x lot + round-trip charges at the stop`.
2. **Lots allowed** = the smallest of:
   - `max_loss_per_trade // loss_one_lot`,
   - `(capital x max_premium_pct_of_capital) // (entry premium x lot)`,
   - `capital // (entry premium x lot)` - the premium is paid in full,
   - `max_lots`.
3. **The order set** for that size: entry, stop-loss trigger, target limit,
   flat-by exit - with `product: NRML` and `validity: DAY`.
4. **The rupee facts**: premium outlay, risk at the stop, reward at the target,
   reward-to-risk **after** the round trip (roughly Rs 45-100 per NIFTY lot).

## When nothing fits

That is a real and common answer, and it is printed as `NOTHING FITS` rather
than rounded up. The menu it gives:

- a **cheaper strike further out of the money** - it moves less in rupees, so
  one lot risks less (and wins less, and decays faster),
- a **tighter premium stop** (which is a smaller chance of surviving noise),
- **SENSEX instead of NIFTY** - lot 20 against 65, so a fifth of the rupee
  exposure for the same index move,
- or **skip the trade**. A trade that does not fit your rules does not become
  safer because you want it.

## Steps

1. Run it after `/trader-timemachine`, from `trader/plan.json`.
2. Tell the user three numbers: lots, premium outlay, and loss at the stop.
3. Say the R after charges, not the gross R. On small premiums the charges are a
   visible share of the reward.
4. Hand off to `/trader-risk`. Do not skip it because the size looks small.

## Example: Monday 9 November 2026

Plan: NIFTY 24,850 PE, entry Rs 246.08, stop Rs 178.34, target Rs 347.69, lot 65.
Config: capital Rs 5,00,000, max loss Rs 5,000 a trade, 25% premium cap, 4 lots max.

```
  one lot risks Rs 4,480 at the stop, charges included
  limits: max loss Rs 5,000 -> 1 lots | premium outlay cap Rs 1,25,000 -> 7 lots |
          cash -> 31 | max_lots 4
  SIZE: 1 lot(s) = 65 units, premium outlay Rs 15,995
  at the stop: Rs 4,480 | at the target: Rs 6,507 | R:R 1.453 after charges (Rs 98 round trip)
```

One lot, because the risk is the binding limit - not the capital, which would
have allowed 31. That is the whole point of sizing by loss instead of by cash:
Rs 5 lakh of capital looks like it can buy a lot of options, and it can, right
up to the point where the stop is hit on all of them.

## Rules

- Size is whole lots, always. `qty` that is not a multiple of the lot is a broker
  rejection or, worse, a partial fill with a mismatched stop.
- Never round a size up to "make the trade worth doing".
- Never print a projected return. Risk and reward at the stop and at the target
  are plan arithmetic, not expectations.
- Charges are part of the size, not an afterthought: they are why a Rs 4 stop
  does not exist.

## What it never does

It never places an order, never edits the plan to make a size fit, never
suggests a bigger max loss, never funds a trade with margin (there is none on
option premium), and never sizes a trade `/trader-exit` refused.

## Safety

- Education and tooling, not investment advice.
- Paper trading by default (`"paper": true`).
- Nothing places an order except `/trader-broker`, after `/trader-risk` passes that exact order and the
  user says yes to it.
- Option buying only by default: `allow_short_premium` is false, because writing options needs margin,
  carries far more risk, and attracts an extra 2% ELM on the expiry day.
