---
name: trader-risk
description: >-
  Risk Manager for Indian index options. The gate between a proposal and the
  broker: lot multiples, 100% upfront premium, max loss per trade with charges,
  reward-to-risk after STT and exchange fees, the day's total risk, trades per
  day, the 09:30-14:30 IST entry window, the flat-by time, expiry-day rules
  (no new entries on the settling contract, the 2% expiry-day margin on shorts),
  the India VIX ceiling, the minimum premium floor, spread, blocked underlyings,
  duplicates and stale contracts. Prints PASS or BLOCK with the reason, and
  records each verdict against a fingerprint of that exact order so only a
  checked order can reach the ticket. Getting out is never blocked. Use when the
  user says "check my order", "is this safe", "risk check", "does this break my
  rules", "can I take this trade", "how much have I risked today", or before
  /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the last thing between a plan and a ticket. Every order
written by `/trader-budget` passes through it. A BLOCK is final for that order:
it does not go to the user for a yes, and it does not go to the broker.

These are rules *you* set in `config.json`. The tool adds only the market
structure it cannot invent: lot size, expiry, the session clock, and the Indian
charge stack.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                            # trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                 # look, do not record
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-11-09 10:45"
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (entry + exits) |
| `trader/config.json` | every limit |
| `trader/state/broker.json` | what was placed today: duplicates, trades per day, the day's risk |
| `trader/state/risk.json` | the verdicts, each tied to a sha256 fingerprint of that exact order |
| contract master + clock | lot, expiry, session state, VIX |

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine one by one but not together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol; side not buy or sell; qty not a positive number; an SL order with no trigger price; a sell with no premium |
| lot | qty is not a multiple of the lot (NIFTY 65, BANK NIFTY 30, SENSEX 20) |
| contract | the symbol is not in today's master - stale, expired or mistyped |
| asset | `allow_options` is off, or the underlying is on your blocked list |
| short premium | a sell that OPENS a position while `allow_short_premium` is false (buying a put is not this) |
| window | before `no_entries_before` (09:30), after `no_entries_after` (14:30), pre-open, post-close, weekend or exchange holiday |
| expiry | fewer days than `min_days_to_expiry`, or an expiry-day entry after `skip_expiry_day_after` (14:00) |
| premium | entry premium under `min_premium` (Rs 20) - illiquid strikes cost more in spread than they can pay |
| cash | the premium outlay exceeds your capital (premium is 100% upfront) |
| size | the outlay is over `max_premium_pct_of_capital`, or lots over `max_lots` |
| loss | loss at the stop, charges included, over `max_loss_per_trade` |
| reward:risk | (target - entry)/(entry - stop) after charges, under `min_reward_risk` |
| daily loss | today's full-size risk plus this one over `max_daily_loss` |
| trades | `max_trades_per_day` entries already placed today |
| regime | India VIX above `max_vix` |
| duplicate | the same symbol and side twice in one batch, or already placed today |
| position | delta-adjusted exposure over 5% of SEBI's Rs 5,000 crore intraday index limit (a warning at retail size) |

**Exits are different.** A sell that only reduces a position is checked for
format and asset only. Getting out must always be possible, so every other rule
becomes a warning on an exit, never a block. A stop-out at 15:29 is allowed.

**Risk is measured from the worse of two prices.** The order's premium and, if
`trader-alerts` has refreshed it, the current premium: a plan written at 10:45
cannot understate the risk at 13:00 after the premium has run.

## Steps

1. If the premium may have moved, refresh it first (`alerts.py check`, or
   re-run `entry.py check`), so the risk is measured on today's price.
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user order by order: the premium, the loss at the stop, the R after
   charges, and how much of today's limits it uses. For a BLOCK give the reason
   and the fix - fewer lots, a cheaper strike, a wider stop (which means fewer
   lots), or skip it.
4. If the order changes in any way, run it again. The ticket matches verdicts by
   fingerprint, so an edited order is not ready until it passes again, and
   yesterday's verdict does not count today.

## Example

```
Risk check - Mon 09 Nov 2026 10:45 IST: open.
India VIX 17.42 (normal)

PASS  #1 buy 65 x NIFTY26N1724850PE
        premium Rs 246.08 | outlay Rs 15,995 | risk at stop Rs 4,480 | reward Rs 6,507 | R:R 1.453
        today before this: 0 of 2 trades, Rs 0 of Rs 15,000 daily risk

1 passed, 0 blocked at 2026-11-09 10:45 IST.
```

Two more lots would be blocked on the daily limit, and the same order at 14:45
would be blocked by the window. Both are correct refusals, not obstacles.

## The Indian rules this encodes

- **Upfront premium** (SEBI, 1 Feb 2025): buyers pay 100% of premium; there is no
  intraday leverage on the premium. Cash is therefore a hard check, not a margin one.
- **Weekly expiry** on one benchmark per exchange (1 Sep 2025): NIFTY Tuesday on
  NSE, SENSEX Thursday on BSE. BANK NIFTY is monthly. The master, not the tool,
  decides which series a contract is.
- **Expiry-day tail risk** (20 Nov 2024): short options expiring that day carry
  an extra 2% extreme loss margin and lose calendar-spread benefit. Retail
  buying is unaffected in margin, but theta and gamma are extreme - hence the
  14:00 cutoff on new expiry-day entries.
- **Intraday position limits** (1 Oct 2025): exchanges check index positions
  intraday, on a delta basis, at random snapshots. Retail size never approaches
  it; the check is here so nobody is surprised at scale.
- **Settlement** (from 7 Dec 2026): expiring index contracts settle on the last
  30 minutes' VWAP and stop at 15:30. Carrying an ITM option into settlement
  also attracts STT on intrinsic value, not premium.

## Rules

- A PASS only means the order may be *shown* to the user. It is not a yes.
- Every number comes from the config, the order, the contract master, or the
  broker log. Nothing is assumed.
- Never loosen a limit to make an order pass. Say which limit it broke and let
  the user decide in their own config.
- A stop is a plan, not a guarantee: in a fast market an SL-M can fill far worse.

## What it never does

It never places an order, never edits an order to make it pass, never blocks an
exit, and never treats a PASS as the user's consent.

## Safety

- Education and tooling, not investment advice. A PASS is a rules check, not a recommendation.
- Paper trading by default.
- Nothing places an order except `/trader-broker`, after this gate passes that exact order AND the user
  says yes to it in the chat.
- No options selling, no futures, no margin unless the config says so.
