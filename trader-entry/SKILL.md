---
name: trader-entry
description: >-
  Entry Timer for Indian index options. Watches NIFTY, BANK NIFTY or SENSEX bar
  by bar, builds the opening range (the first 15 minutes of the 09:15 session by
  default) and calls the entry on the first completed 5-minute bar that CLOSES
  beyond it, or beyond a level you give it. Then it picks the option that
  expresses the signal - ATM by default, or by delta - names the exact exchange
  symbol, the lot size, the expiry, the premium, the greeks and the spread, and
  writes trader/entry.json for /trader-exit. Says waiting and how far,
  triggered with the time and the premium, expired, or closed. It never places
  an order and never buys a strike it has not priced. Use when the user says
  "tell me when to get in", "watch Nifty", "has it broken out", "did Nifty
  break the opening range", "which option do I buy", "entry signal", "time my
  entry", or after /trader-scanner and /trader-news, before /trader-exit.
argument-hint: "[check | watch | watchlist] [NIFTY] [--setup orb|level] [--strike atm|delta]"
---

# trader-entry

The Entry Timer turns a setup into one moment and one contract. Two things
matter in Indian options and both are decided here:

1. **The moment.** A price that pokes through the level mid-bar is not an
   entry. A bar that CLOSES beyond it is. Bars are 5-minute bars on the
   exchange's own grid (09:15, 09:20, ... 15:25), and a bar counts only once
   its five minutes are over.
2. **The contract.** The signal is on the index; the money is in the premium.
   A NIFTY call moves about Rs 32 for every 100 index points when delta is
   0.32 - so the strike, the expiry (8 days or same-day), the lot (65 units)
   and the spread decide whether the signal is worth anything.

It never buys, never sizes, and never places anything.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check NIFTY              # 15-min OR, 5-min bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check NIFTY --write       # -> trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check NIFTY --setup level --level 25100
python3 "${CLAUDE_SKILL_DIR}/entry.py" check SENSEX --strike delta
python3 "${CLAUDE_SKILL_DIR}/entry.py" check NIFTY --offset 2    # two strikes OTM
python3 "${CLAUDE_SKILL_DIR}/entry.py" check NIFTY --interval 1m --strike atm
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch NIFTY --until 14:30 # one line per closed bar
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                 # every hit in the watchlist
```

Flags: `--setup orb|level`, `--level PRICE`, `--interval 5m|1m`,
`--strike atm|delta`, `--offset N`, `--chain auto|dhan|nse|bse|csv|model`,
`--chain-file PATH`, `--or-minutes N`, `--at HH:MM`, `--write`, `--json`,
`--now "2026-11-09 10:45"` to replay a session. `watch` with `--now` replays
the session bar by bar without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits, for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30), `no_entries_after` (14:30), `opening_range_minutes`, `strike_selection`, `target_delta`, `min_premium`, `allow_expiry_day_entries`, `skip_expiry_day_after` |
| Yahoo index bars | today's 5-minute (or 1-minute) bars for the index |
| contract master | expiry, lot, strike step, exact trading symbol |
| option chain | **Dhan** (real quotes, OI, greeks, bid/ask when `DHAN_ACCESS_TOKEN` is set), NSE, BSE, your exported CSV, or the model |
| `trader/entry.json` | with `--write`, or when a watch triggers |

## The rule

- **Opening range.** The high and low of the first 15 minutes: the 09:15, 09:20
  and 09:25 bars. The level to beat is the range high (call) or range low (put).
- **Level.** A price you give it, for example a 20-session high from the scanner.
- **The trigger** is the first completed bar after the range that CLOSES beyond
  the level. Equal to the level is not beyond it. One trigger per session.
- **The time** is that bar's close time: the 10:40 bar closes at 10:45 IST.
- **The option.** ATM by default (the strike nearest the trigger price), or by
  delta (`--strike delta` picks the strike closest to `target_delta`, 0.5 by
  default). `--offset 2` steps two strikes out of the money, which is cheaper
  but moves less per index point.
- **The expiry** is the next tradable one: today's is skipped after 15:30 and
  skipped entirely unless `allow_expiry_day_entries` is on, because buying an
  option on its expiry morning is a different game (gamma and theta both spike).
- **Time limits.** No new entries before 09:30 or after 14:30 IST, and on
  expiry day none after 14:00. These are in config, not in the tool.

## What it says, and what it refuses

- `WAITING` with the distance to each edge of the range, and the last close.
- `TRIGGERED` with the bar's close time, the index price, the exact symbol
  (`NIFTY26N1724850PE`), the lot, the premium, the greeks, the spread, and the
  premium's source.
- `BLOCKED` when the contract breaks a rule: premium under the Rs 20 floor,
  spread wider than `max_spread_pct`, expiry-day cutoff passed, no premium at all.
- `market-closed`, `not-open` (pre-open auction), `no-data`, `error`.

## Steps

1. Get the index from the user, or run `entry.py watchlist` after the scanner.
2. Run `/trader-news` for that index first. A breakout into an RBI decision is
   not the same trade as a breakout into nothing.
3. `entry.py check NIFTY`. Say the status in one line: waiting and how far,
   triggered at what time and at what premium, or closed.
4. To wait for it, run `entry.py watch NIFTY --until 14:30` in the background,
   or `check --write` on a loop every five minutes.
5. When it triggers, say the time, the premium and the source of that premium -
   and say that nothing was placed. If the trigger is more than a bar old, say
   that too: a late entry is a different trade.
6. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Example: Monday 9 November 2026, NIFTY

The opening range was 24,899.40 to 25,081.00. The first completed bar to close
outside it was the 10:40 bar, at 24,845.75 - below the range low. So:

```
NIFTY      TRIGGERED    10:45 IST - closed below the range low
           option: NIFTY26N1724850PE  (NIFTY 50 24,850 PUT, weekly expiry 17 Nov 2026, lot 65, 8d)
           premium: Rs 246.08 x 65 = Rs 15,995 a lot - premium MODELLED from the index level and India VIX
           greeks: delta -0.48, gamma 0.0006, theta -13.98/day, IV 17.4%
           spread: 1.02% of the premium
           India VIX 17.42 (normal)
           Nothing was placed.
```

Read that theta line before anything else: Rs 14 a day per unit is Rs 910 a day
on one lot. That is the cost of being early, and it is why `/trader-exit` sets a
time stop as well as a premium stop.

## Rules

- Bar by bar, on free Yahoo bars. They are unofficial and can lag; say so when
  a minute matters.
- A signal is information, not an order, and not a promise.
- One side per session per setup: the first close beyond the range. Later
  closes do not reset it.
- Never call an entry early because a bar looks like it will close beyond.
- Long premium only by default. Buying a put is a bearish trade but not a short
  sale; writing options is switched off in config.
- Never present a modelled premium as a quote. If the chain source is `model`,
  say so in the same sentence as the price.

## What it never does

It never places an order, never sizes a position, never sets the stop (that is
`/trader-exit`), never moves the level after the fact, and never counts a bar
that has not closed. It never runs `/trader-broker` from a background watch:
nobody is there to say yes.

## Safety

- Education and tooling, not investment advice. A trigger is not a recommendation.
- Paper trading by default (`"paper": true`).
- Nothing in this pack places an order except `/trader-broker`, and only after `/trader-risk` passes that
  exact order AND the user types an explicit yes for it.
- No options selling, no margin funding, no futures unless the config switches them on.
- Broker tokens live in environment variables, never in the chat.
