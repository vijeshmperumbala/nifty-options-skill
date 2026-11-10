---
name: trader-journal
description: >-
  Trade Journal for Indian index options. Records every entry and exit - the
  paper fills from /trader-broker go in automatically - and reports what the
  record actually says: win rate, average win and loss in rupees, expectancy in
  R, profit factor, charges paid, max drawdown, and the same numbers broken down
  by index, by call versus put, and by setup. Warns when the sample is too small
  to mean anything. Use when the user says "how am I doing", "my stats", "win
  rate", "review my trades", "trade journal", "expectancy", "am I profitable",
  "log this trade", or at the end of a week or month.
argument-hint: "[stats | list | add] [--by underlying|right|symbol|setup]"
---

# trader-journal

Every other skill in this pack tries to stop one bad trade. This one tries to
stop a hundred, by making the record unavoidable.

Indian option buyers have a specific failure mode: a 70% win rate and a losing
account, because the wins are Rs 800 and the losses are Rs 4,000. Only the R
column shows that. So the journal reports in R alongside rupees - R being
profit divided by the risk that was planned at the stop, which is the only
number that is comparable across a Rs 90 put and a Rs 400 call.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/journal.py" stats
python3 "${CLAUDE_SKILL_DIR}/journal.py" stats --by right
python3 "${CLAUDE_SKILL_DIR}/journal.py" stats --by setup
python3 "${CLAUDE_SKILL_DIR}/journal.py" list --limit 30
python3 "${CLAUDE_SKILL_DIR}/journal.py" add --symbol NIFTY26N1724850PE \
    --entry 246.08 --exit 210.50 --qty 65 --risk 4480 --note "stopped on time"
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/journal.jsonl` | one line per entry, one per exit; written by `/trader-broker` and by `add` |
| `trader/state/broker.json` | open positions, so it can say what is still running |

## What it reports

| number | what it means | the trap it exposes |
| --- | --- | --- |
| win rate | share of closed trades with a profit | 70% wins with 0.2R average is a losing system |
| average win / average loss | in rupees | the ratio that decides everything |
| expectancy (R) | average R per trade | negative means stop trading this way |
| profit factor | gross wins / gross losses | under 1.0 is losing money |
| charges paid | what the brokers and the government took | on small premiums this is 1-3% a trade |
| max drawdown | worst peak-to-trough in rupees | the number that decides your position size |
| buckets | by index, call/put, setup | the one bucket that pays for all the others |

## The one honest caveat

Under 30 closed trades, almost nothing is significant. The tool says
"this is a sample, not a track record" and it means it: a 4-trade winning streak
is noise, and so is a 4-trade losing one. The value of the journal before 30
trades is that it stops you from *thinking* your win rate is 90% when it is 45%.

## Steps

1. Log every trade. The paper fills do it for you; a trade you took in your own
   terminal can be added with `journal.py add`.
2. At the end of the week, run `stats` and read the buckets. Look for the bucket
   with the negative R, not the positive one.
3. Compare the average loss in R to -1.0. If it is worse than -1, the stops are
   not being honoured - which in options usually means a gap or a skipped SL-M,
   not indiscipline. Say which.
4. Compare charges to the average win. If charges are 15% of the average win,
   the strategy has to be right a lot more often than it looks.

## Example

```
Journal - 1 closed trade(s), 0 still open
  win rate 0.0%  (0W / 1L)
  total P&L Rs -85; charges paid Rs 85
  average win Rs 0, average loss Rs -85, profit factor 0.0
  expectancy -0.019R a trade; max drawdown Rs -85

  by underlying:
underlying  trades  win%  P&L  avg R
NIFTY            1   0.0  -85  -0.02

  Fewer than 30 trades: this is a sample, not a track record.
```

Note what that example is: a paper position flattened at the same premium it was
bought at, so the entire loss is charges. That is the most common first entry in
any Indian options journal, and it is worth seeing.

## Rules

- Never round a win rate up, and never drop a losing trade from the sample.
- R is measured against the risk that was PLANNED, not the risk that happened.
- Charges are part of the P&L, not a footnote.
- Never present a return as a percentage of capital without also giving the R:
  a 12% gain on one trade and a 12% gain on a year are different claims.
- No extrapolation. Eight trades do not make a system.

## What it never does

It never places an order, never invents a trade, never deletes one, never
smooths a drawdown, and never turns a small sample into a conclusion.

## Safety

- Education and tooling, not investment advice, and not tax advice: F&O income is
  business income, and the audit thresholds depend on turnover and on how it is
  computed. Talk to an accountant.
- Paper trading by default; a paper journal and a real one look the same here,
  which is why every row carries a `paper` flag.
- Nothing places an order except `/trader-broker`, after `/trader-risk` passes that exact order and the
  user says yes to it.
