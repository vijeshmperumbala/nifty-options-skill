---
name: trader-news
description: >-
  News Monitor for the Indian market. Pulls recent NIFTY, Sensex and Bank Nifty
  headlines from Google News RSS, stamps them in IST with their age and source,
  tags the risk each one implies (monetary policy, macro data, global rates,
  flows, regulation, expiry), and prints the scheduled-risk list that can
  invalidate a day trade - RBI decisions, CPI prints, US data after Indian
  hours, expiry mornings. Free, no key. Use when the user says "what is the news",
  "why is Nifty moving", "any news on Nifty", "market news today", "what happened
  overnight", "RBI policy today", "event risk", or after /trader-scanner and
  before /trader-entry.
argument-hint: "[--underlying NIFTY] [--hours 12] [--query 'RBI policy'] [--events]"
---

# trader-news

A headline is not a direction. It is, however, a reason to check whether your
trade is exposed to something that could arrive before your stop does. In India
the scheduled risks are specific:

- **RBl policy** (MPC decisions, six times a year, announced around 10:00 IST):
  moves the whole index within minutes, and both call and put buyers can lose
  because implied vol collapses after the announcement.
- **CPI, IIP, GDP** at 17:30 IST - after the close, so they change tomorrow's
  09:15 gap, not today's trade.
- **US Fed and US CPI** land after Indian hours; the gap appears at the open.
- **Expiry mornings**: NIFTY Tuesday, SENSEX Thursday. Gamma and theta both peak,
  and a "cheap" option is cheap because it is about to expire.

A day trade that is flat by 15:15 is not exposed to anything that lands after
15:30. That is the only reliable protection this pack offers, and it is enough.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                          # market-wide, last 12h
python3 "${CLAUDE_SKILL_DIR}/news.py" --hours 6
python3 "${CLAUDE_SKILL_DIR}/news.py" --underlying BANKNIFTY
python3 "${CLAUDE_SKILL_DIR}/news.py" --query "RBI monetary policy"
python3 "${CLAUDE_SKILL_DIR}/news.py" --events                  # just the scheduled-risk list
python3 "${CLAUDE_SKILL_DIR}/news.py" --json
```

## What it reads and writes

| file | what |
| --- | --- |
| Google News RSS | free, no key, `hl=en-IN&gl=IN`: Indian editions of the wires |
| Yahoo | NIFTY, BANK NIFTY, SENSEX quotes and India VIX, for the snapshot |
| `trader/news.json` | the headlines, tags, snapshot and the scheduled-risk list |

## Steps

1. Run it before `/trader-entry`, every session.
2. Say the headlines by age, with the tag that matters: `[monetary policy]`,
   `[macro data]`, `[expiry]`.
3. Say the VIX level and the day's index move - the market's own summary.
4. Say whether the trade being planned would be open when the next scheduled
   risk lands. If it would, say that a day trade closed by 15:15 is not exposed.
5. Never say "the news is bullish" or "buy calls because of X". Point at the
   risk, not the direction.

## Example

```
Market news - Mon 09 Nov 2026 19:12 IST: post.
  NIFTY        25,044.65  +0.23%
  BANKNIFTY    59,741.25  +1.11%
  SENSEX       81,106.65  +0.10%
  India VIX        17.42  -5.48%  (normal)

3 headline(s) in the last 12h:
    42m  Nifty ends at record high as banks rally; VIX slips below 18  [flows]
        Reuters
   3h11m  RBI holds repo rate, signals data-dependent path ahead  [monetary policy]
        Business Standard
```

## Rules

- Every headline keeps its source and its age in IST. An undated headline is not
  news, it is a screenshot.
- Tag the risk; never infer a direction. "RBI holds rates" is not a buy signal,
  and saying so is not analysis.
- Google News is a feed of other people's headlines: it lags, it duplicates, and
  it carries sponsored content. Never present it as verification.
- If the feed returns nothing, say "no headlines came back" - not "no news".
- Say the scheduled-risk list every time. It is the part that actually changes
  decisions.

## What it never does

It never places an order, never recommends a direction, never claims to know
what moved the market, never quotes a headline as fact without its source, and
never treats a rumour as a signal.

## Safety

- Education and tooling, not investment advice.
- Paper trading by default.
- Nothing places an order except `/trader-broker`, after `/trader-risk` passes that exact order and the
  user says yes to it.
