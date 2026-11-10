---
name: trader-alerts
description: >-
  Price Alerts for Indian index options. Turns a plan into watches in one
  command - the premium stop, the premium target, the 15:15 IST flat-by time and
  the expiry date - and adds index-level alerts (NIFTY above or below a level),
  premium alerts on an exact trading symbol, and clock alerts. Checks them once,
  rings what has newly hit, and never rings the same alert twice. Built to be run
  on a loop or a scheduled task, because in options the exit matters more than
  the entry and nobody is watching at 14:57. It only reads prices: it cannot and
  will not place an order. Use when the user says "alert me", "watch this level",
  "tell me when it hits my stop", "remind me to square off", "set a price alert",
  "ping me at 3:15", or right after /trader-broker fills.
argument-hint: "[add | check | list | clear] [--from-plan] [--underlying NIFTY --above 25150]"
---

# trader-alerts

An option position has three ways to end - the stop, the target, and the clock -
and only one of them is a price you are watching. On expiry day the clock is the
one that hurts: theta takes the premium while you wait for a move that never
comes, and the broker squares off whatever is left at the close.

This skill turns the plan into watches and rings them once. It is the least
glamorous skill in the pack and probably the one that saves the most money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan          # stop + target + flat-by + expiry
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --underlying NIFTY --above 25150
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --underlying NIFTY --below 24850 --note "range low"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --premium NIFTY26N1724850PE --below 178.34
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --time 15:15 --note "flat by"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                    # rings what hit, once
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clear
```

Run `check` on a loop - `/loop 1m`, a cron job, or a terminal tab - or wire it
to your notification of choice. Exit code 0 when something rang, 1 when nothing did.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | `add --from-plan`: stop, target, flat-by, expiry |
| `trader/state/alerts.json` | the armed alerts and what already rang, so nothing rings twice |
| Yahoo index quotes | index-level and premium watches |
| contract master | to find the exact symbol's strike and expiry |

## The four kinds of alert

| kind | example | what it is for |
| --- | --- | --- |
| `premium` | `--premium NIFTY26N1724850PE --below 178.34` | the money stop and the money target |
| `index` | `--underlying NIFTY --below 24850` | the level where the setup failed, so you can see it coming |
| `time` | `--time 15:15` | flat-by; the one exit that is never wrong |
| `date` | `--on 2026-11-17` | expiry morning: no new entries, tighten the plan |

`--from-plan` arms the first, third and fourth in one go, straight from
`trader/plan.json`, using the exact stop and target premiums the plan computed.

## Steps

1. Right after `/trader-broker` fills, run `add --from-plan`.
2. Add the index-level version of the stop as well - an alert on the index fires
   before the premium alert, because the premium lags and the spread widens as
   it does.
3. Add the 14:30 entry cutoff as a time alert if you are still looking for trades
   at midday; the cutoff exists to stop the 14:59 impulse.
4. Run `check` on a loop while the position is open, and once after 15:20 to
   confirm you are flat.
5. When an alert rings, say what it means, check the price in the terminal, and
   let the user decide. An alert is not an action.

## Example

```
$ alerts.py add --from-plan
added premium  NIFTY26N1724850PE <= Rs 178.34   STOP: or-mid at index 24990.2
added premium  NIFTY26N1724850PE >= Rs 347.69   TARGET
added time     15:15 at 15:15 IST               flat by: close whatever is open
added date     2026-11-17 on 2026-11-17         expiry day: no new entries, be flat early

$ alerts.py check
Alert check at 14:07:12 IST (4 alert(s) armed)
  RING  NIFTY26N1724850PE Rs 175.90 at or below Rs 178.34  [STOP: or-mid at index 24990.2]

An alert is information: check the price in your terminal, then act yourself.
```

## Rules

- Ring once. An alert that repeats every 30 seconds trains you to ignore it.
- Say which price was used: the free Yahoo quote, or the model. On a modelled
  premium the alert is a calculation, not a market print - say so.
- A premium alert on a wide spread can fire on the bid alone. Use the index
  alert as the second opinion.
- Never present an alert as an instruction, and never say "your stop was hit" -
  say which price printed and where it came from.

## What it never does

It never places an order, never moves a stop, never repeats a fired alert,
never watches a symbol it cannot price without saying so, and never runs
`/trader-broker` on your behalf.

## Safety

- Education and tooling, not investment advice.
- Paper trading by default; alerts work the same in either mode.
- An alert is information. The decision, and the order, remain yours.
- Nothing in this pack places an order except `/trader-broker`, after `/trader-risk` passes that exact
  order and the user says yes to it.
