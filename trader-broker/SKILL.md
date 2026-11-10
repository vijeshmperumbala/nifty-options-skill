---
name: trader-broker
description: >-
  Order Executor for Indian index options on Dhan. Shows the ticket in plain
  words (symbol, side, lots, premium paid upfront, stop, target, expiry), checks
  the live quote against the plan, paper-fills it - or, when you have turned live
  trading on deliberately, places the real Dhan order with a stop and a target
  attached. Every path needs a /trader-risk PASS for that exact order today plus
  an explicit yes. Tracks open positions in premium and in R, reconciles them
  against Dhan, and flattens them. Use when the user says "show me the ticket",
  "place the order", "buy it", "paper trade it", "what is my position",
  "close it", "square off", or after /trader-risk.
argument-hint: "[show | preflight | place | status | flatten | payload | book] [--yes]"
---

# trader-broker

The only skill in the pack that can lead to a position at a broker. Default mode
is paper, where the position is a row in a JSON file. Live mode places real
orders on Dhan, and only when four things line up - never by itself.

## The locks

1. `/trader-risk` PASSED **this exact order** today. The match is by fingerprint
   (a hash of symbol, side, qty, order type, prices, product, exchange), so an
   edited order needs a fresh verdict.
2. You said yes **for this ticket**, in this conversation.
3. `config.json` says all three of: `paper: false`, `live_broker: "dhan"`,
   `live_trading_enabled: true`. One switch left off still sends nothing.
4. Preflight passes at the moment of sending: token present, market open before
   `flat_by`, the real premium within `max_entry_slippage_pct` of the plan, cash
   for the premium, and a Dhan `security_id` for that exact contract.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show            # the ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" preflight       # every live check, sends nothing
python3 "${CLAUDE_SKILL_DIR}/ticket.py" place --yes     # paper fill, or a real Dhan order
python3 "${CLAUDE_SKILL_DIR}/ticket.py" status          # open positions, premium P&L, R, broker state
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --yes   # cancel the exits, sell at market
python3 "${CLAUDE_SKILL_DIR}/ticket.py" payload         # Dhan + Kite/Upstox/Angel One field names
python3 "${CLAUDE_SKILL_DIR}/ticket.py" book            # today's Dhan orders and positions
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the order set from `/trader-budget` |
| `trader/state/risk.json` | today's verdicts, matched by fingerprint |
| `trader/state/broker.json` | what was placed, what is open, the broker's order ids |
| `trader/state/live.json` | the last live order and its status, for a post-mortem |
| `trader/journal.jsonl` | every entry and exit; `paper` tells you which was real |

## The ticket, in plain words

```
READY     BUY 65 units (1 lot) of NIFTY26N1724850PE on NFO
          premium Rs 246.08 a unit  ->  Rs 15,995 paid upfront
          stop   Rs 183.24  (index 24990.2)
          target Rs 340.34  (index 24669.08)
          risk at the stop Rs 4,162, reward Rs 6,030, R:R 1.449
          order type market, product NRML, validity DAY, expiry 2026-11-17
          premium source -
          fingerprint dd02fb484e16f84e - verdict PASS
```

Read it out loud before saying yes. The three numbers that matter are the
premium leaving your account, the loss if the stop is hit, and the fact that both
happen in the same second you fill.

## Preflight, which is the point of the whole thing

```
fingerprint dd02fb484e16f84e
  real premium Rs 246.10 vs plan Rs 246.08 (+0.01%)
  security id 63411 on NSE_FNO, premium outlay Rs 15,996, cash Rs 285,341
  WARN  order placement needs a STATIC IP whitelisted on your Dhan account
  preflight PASS - nothing has been sent
```

It only reads: the chain for that exact expiry, the fund limit, the calendar. A
plan premium is a guess made a few minutes ago; the quote is not. If the option
has repriced more than `max_entry_slippage_pct` (default 2%) above the plan, the
answer is no - the stop and target were sized for the number you approved, and
re-run `/trader-entry` if you still want the trade at the new price. Slipping
*down* by more than 8% is a warning, not a block: it usually means the index
moved past the trigger and the plan is stale.

## Going live with Dhan, in order

1. **Data subscription.** Dhan's Data APIs (option chain, quotes) are a paid
   add-on on top of the free trading API; without it the chain calls answer
   `DH-902`/`806`. Verify the current price on Dhan's pricing page before paying
   for anything.
2. **Credentials, in the environment, never in the chat or in `config.json`:**

   ```bash
   export DHAN_CLIENT_ID="1000xxxxxx"
   export DHAN_ACCESS_TOKEN="eyJ..."      # DhanHQ v2 token, about 30 days
   ```

   A token that expires mid-session fails as `DH-901`/`807`, which this skill
   reports in those words instead of a stack trace.
3. **Static IP.** Order placement, modification and cancellation need a static
   IP whitelisted on your Dhan account (SEBI's algo rule). A working token is not
   enough - Dhan's own docs say so, and a rejected order here carries
   `omsErrorDescription` back verbatim so you can see it.
4. **The three config switches**, all at once, on purpose:

   ```json
   { "paper": false, "live_broker": "dhan", "live_trading_enabled": true }
   ```

5. **Prove it without money.** Run `ticket.py preflight` until it says PASS. It
   resolves the security id, reads the fund limit and prices the contract - and
   sends nothing.

## What a live fill actually does

```
sending BUY: {"transactionType": "BUY", "exchangeSegment": "NSE_FNO", "orderType": "MARKET", ...}
  entry order 1123091024730 -> TRANSIT
  FILLED 65 units at Rs 246.10 (charges about Rs 30.73)
  stop   order 1123091024732 at Rs 183.24 (trigger), qty 65
  target order 1123091024733 at Rs 340.34 (limit), qty 65
```

Then, honestly:

- **Dhan has no OCO.** The stop and the target are two separate orders. When one
  fills the other keeps working, and you are short 65 units you do not own.
  `status` catches this and cancels the orphan; `flatten` cancels both first.
- **The fill price is the broker's**, not the plan's. Size, R and the journal all
  use `averageTradedPrice`.
- **A missing exit leg is loud.** If the stop cannot be placed after a fill, the
  output says so in capitals and tells you to place it by hand or flatten now. It
  never pretends the position is protected.
- **No retries.** A rejected or still-pending entry is reported and stops there;
  nothing is resubmitted behind your back.

## When Dhan refuses

| code | what happened | what to do |
| --- | --- | --- |
| `DH-901`, `807`, `808`, `809`, `810` | token expired or wrong client id | regenerate the access token in Dhan's web console |
| `DH-902`, `806` | no Data API subscription | subscribe, or stay on the MODELLED chain |
| `DH-904` | rate limit | the chain allows one request every 3 s; this module throttles and caches |
| `DH-905` | bad or missing field | check the payload with `ticket.py payload` |
| `DH-906` | Dhan refused the order | static IP, margin, freeze quantity, or market state |
| `DH-907`, `811`, `813` | no data / bad expiry / bad security id | re-run `/trader-entry`; the contract may have moved |
| `omsErrorDescription` on a REJECTED order | Dhan's own words | read it; it usually names the missing whitelist |

The emergency stop: `POST /v2/killswitch?status=activate` blocks all order
placement on the account until deactivated. `trader.py` here does not call it
automatically - killing a switch is a decision, not a fallback.

## Living with Indian broker mechanics

- **Products.** `live_product` defaults to `CNC` (delivery) because a bought
  option has to survive to expiry if it must; `INTRADAY` is cheaper on margin and
  is force-squared off earlier than you may want.
- **Expiry day.** A bought option on its expiry day must be sold before 15:30 or
  it settles for intrinsic, and STT is then charged on that intrinsic value.
- **The premium is paid in full.** No leverage, no margin funding.
- **Freeze quantity.** Far above one or four lots, so irrelevant here; above it,
  Dhan's order-slicing endpoint would be needed.

## Steps

1. `ticket.py show`. If anything says NOT READY, stop and read the reason.
2. Say the ticket in plain words and ask for an explicit yes for that exact
   order. An earlier yes is not consent for a changed one.
3. `ticket.py preflight` - read the real premium against the plan, out loud.
4. On yes: `ticket.py place --yes`. Paper fills at the plan premium; live fills at
   the broker's price and attaches both exits.
5. Immediately set the watches: `/trader-alerts` for the stop, the target and the
   flat-by time.
6. When it closes, `/trader-journal` records it. `ticket.py status` in between
   reconciles against Dhan and cancels orphaned exit legs.

## Rules

- Never place anything without the user's explicit yes for that exact ticket.
- Never place an order `/trader-risk` did not pass today.
- Never turn live trading on for the user, never suggest it to "make it real",
  and never put a token in a file, a chat message or a command line.
- Never place an order when preflight has a BLOCK - including the static-IP
  warning being unaddressed and an unexplained rejection.
- Never print a projected return; P&L is only ever the fill or the mark.
- Say "paper" every time a fill is a simulation, and "live" every time it is not.

## What it never does

It never sends an order without a fresh PASS and an explicit yes, never hedges a
ticket on its own, never edits an order to make it pass, never sizes a position,
never keeps a position past the flat-by time of its own accord, never retries a
rejection, and never reports a paper fill as a real one.

## Safety

- Education and tooling, not investment advice.
- Paper trading by default (`"paper": true`); going live takes three deliberate
  switches and a passing preflight.
- Credentials live in environment variables only.
- No options selling, no futures, no margin unless the config allows it and you
  confirm.
