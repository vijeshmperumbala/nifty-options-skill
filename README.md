# The NIFTY / SENSEX Options Trader skill

Eleven Claude skills for trading Indian index options: scan the NIFTY 50, BANK
NIFTY and SENSEX charts, read the news and the option chain, time the entry bar,
set the premium stop and target before you risk anything, replay the rest of the
session 10,000 times in premium, size in whole lots, gate the order against your
own limits, and keep the record.

A conversion of [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(the US day-trading pack) to the Indian market, where the tradable thing is an
**index option on NSE or BSE**, not a share.

Free, MIT, no signup, no API key. The tools are standard-library Python that read
free public data: Yahoo index bars, the exchange's own contract master, Google
News RSS, and an option chain from NSE/BSE or a CSV you export.

> **Not investment advice.** Software that reads public data and does arithmetic.
> Most retail option buyers in India lose money - SEBI has said so repeatedly, and
> its own studies put the share of loss-making F&O traders above 90% in some
> years. You can lose money. You can lose all of the premium you pay.

---

## What changed in the conversion

The US pack trades shares on a 09:30-16:00 New York session. Almost nothing
carries over unchanged, and the differences are the whole point:

| | US original | this pack |
| --- | --- | --- |
| Session | 09:30-16:00 ET | **09:15-15:30 IST**, pre-open auction 09:00-09:15, no after-hours |
| Instrument | shares of a US stock | **index options**: NIFTY 50, BANK NIFTY, SENSEX (European, cash-settled) |
| Lot size | 1 share | **whole lots only**: NIFTY 65, BANK NIFTY 30, SENSEX 20 (Nov 2026 master) |
| Expiry | none (a share does not expire) | **weekly** NIFTY Tuesday, SENSEX Thursday; BANK NIFTY monthly |
| What you lose | price difference | **premium, which decays**: theta, gamma, IV crush |
| Sizing | `max_loss / (entry - stop)` shares | same maths, then floor to **whole lots** - often the answer is "one lot is already too big" |
| Costs | SEC/FINRA fees, ~0 | **STT 0.15% of sell premium, exchange 0.035%, GST 18%, stamp 0.003%** - about Rs 45-100 per NIFTY lot round trip |
| Leverage | margin account | **100% premium upfront**, no leverage on premium (SEBI, 1 Feb 2025) |
| Position limits | PDT rule | **SEBI intraday index limits**, delta-based, checked 4x a day |
| Automation | Alpaca MCP | **Dhan API, on purpose**: paper by default, real orders only behind three config switches, a risk PASS and your yes (static IP + registered algo still required by SEBI) |
| Calendar | NYSE holidays | **NSE/BSE holidays**, expiry shifts included (2026 list embedded) |
| Data | Yahoo + SEC EDGAR | Yahoo indices + contract master + option chain (NSE/BSE/CSV/model) |
| "Follow the money" skill | Pelosi's congressional filings | **your own trade journal** - there is no useful Indian equivalent |

The eleven skills map like this:

| US | India | why |
| --- | --- | --- |
| `/trader-scanner` | `/trader-scanner` | same job, now on three indices instead of 5,900 charts |
| `/trader-news` | `/trader-news` | Google News RSS in the Indian edition, plus RBI/CPI/expiry risk tags |
| `/trader-entry` | `/trader-entry` | now also picks the option: strike, expiry, lot, premium, greeks |
| `/trader-exit` | `/trader-exit` | now translates the index stop into a **premium** stop |
| `/trader-timemachine` | `/trader-timemachine` | replays **repricing the option** every bar, so theta is in the odds |
| `/trader-budget` | `/trader-budget` | sizes in **lots**, with the Indian charge stack inside the R |
| `/trader-risk` | `/trader-risk` | lot multiples, upfront premium, expiry-day rules, VIX ceiling |
| `/trader-broker` | `/trader-broker` | paper fills by default, **real Dhan orders when you turn live on**, plus Kite/Upstox/Angel One payloads to copy |
| `/trader-alerts` | `/trader-alerts` | premium, index, flat-by 15:15 and expiry watches |
| `/trader-analyst` (earnings) | `/trader-analyst` (**option chain**) | for an index there is no earnings report; there is a chain, a VIX and a calendar |
| `/trader-pelosi` (congress) | `/trader-journal` (**your trades**) | tracking a politician is not a strategy; tracking your own R is |

## Install

Copy the **whole folder** (the skills share `lib/`):

```bash
git clone <this repo> nifty-options-skill
cp -r nifty-options-skill/trader-* ~/.claude/skills/
cp    nifty-options-skill/lib        ~/.claude/skills/
```

Or project-local, into `<your repo>/.claude/skills/`. Installed as a Claude Code
plugin they appear as `/nifty-options:trader-scanner` and so on.

No Claude Code at all? Paste any single `SKILL.md` at the top of a chat and it
runs as a mode. You lose the Python tools, but the method works.

Python 3.9 or newer (3.8 works except for `zoneinfo` on Windows). Nothing to pip
install. Optionally, once per machine:

```bash
mkdir -p trader && cp trader-budget/config.example.json trader/config.json
```

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Index Scanner | Finds breakout, breakdown, flag and EMA setups on the three indices; finds the opening-range break intraday. |
| `/trader-news` | News Monitor | NIFTY/Sensex headlines in IST, tagged by the risk they imply, plus the scheduled-risk list. |
| `/trader-entry` | Entry Timer | Watches the index bar by bar and calls the entry on a CLOSED bar beyond the level - then names the exact option, lot, expiry and premium. |
| `/trader-exit` | Exit Planner | Sets the stop on the index where the setup failed and translates it into the premium you actually lose; target in R of the premium; flat by 15:15. |
| `/trader-timemachine` | Time Machine | 10,000 replays of the rest of the session with the option repriced every bar, plus the same rule on 60 past sessions. |
| `/trader-budget` | Budget Manager | Sizes in whole lots from your max loss, and says "nothing fits" when one lot is already too big. |
| `/trader-risk` | Risk Manager | PASS or BLOCK: lots, upfront premium, loss at the stop, R after charges, daily loss, entry window, expiry-day rules, VIX ceiling. |
| `/trader-broker` | Order Executor | Shows the ticket, checks the live quote against the plan, paper-fills it on your explicit yes - or places the real Dhan order when you have turned live trading on deliberately. Tracks it in premium and R, flattens it. |
| `/trader-alerts` | Price Alerts | Premium stop, premium target, index levels, flat-by 15:15, expiry morning. Rings once. |
| `/trader-analyst` | Options Analyst | PCR, max pain, OI walls, ATM straddle, expected move, skew, India VIX regime, expiry calendar. |
| `/trader-journal` | Trade Journal | Win rate, expectancy in R, profit factor, charges, drawdown - by index, by call/put, by setup. |

## The workflow

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker
                                                                       |
alerts: premium stop, premium target, flat by 15:15         journal <-+
```

Each skill reads what the last one wrote, in `trader/`:

```
trader/watchlist.json   scanner -> news, entry
trader/entry.json       entry   -> exit
trader/plan.json        exit    -> timemachine, budget, alerts
trader/orders.json      budget  -> risk
trader/state/risk.json  risk    -> broker (matched by fingerprint)
trader/journal.jsonl    broker  -> journal
```

## A worked example (real data, Monday 9 November 2026)

NIFTY's opening range was 24,899.40 to 25,081.00. The first completed 5-minute
bar to close outside it was the 10:40 bar, at 24,845.75 - **below** the range, so
the put side.

1. `/trader-scanner daily` had tagged NIFTY a 20-session closing-high breakout
   (close 25,044.65 against the prior high 25,030.35) and RSI(14) 78.6. Both
   statements were true and neither was tradable: the real break that morning
   went the other way.
2. `/trader-entry NIFTY` at 10:45: `TRIGGERED`, ATM put **NIFTY26N1724850PE**,
   weekly expiry 17 Nov 2026, lot 65, premium Rs 246.08 (modelled: Rs 246.08 x 65
   = Rs 15,995 a lot), delta -0.48, theta -13.98 a day, IV 17.4%.
3. `/trader-exit`: stop at the range midpoint 24,990.20 → premium Rs 178.34, so
   Rs 67.74 a unit at risk; target 1.5R → Rs 347.69 (index 24,648.95); charges
   Rs 98 round trip; **R:R 1.45 after costs**. Flat by 15:15.
4. `/trader-timemachine`: 10,000 replays, seed 19 → target first 5.9%, stop first
   13.7%, neither 80.4%, green 52.7%. The verdict: the plan is wider than an
   afternoon of NIFTY, and four replays in five end with the premium
   approximately where it started, minus theta.
5. `/trader-budget`: one lot risks Rs 4,480 at the stop, charges included. With a
   Rs 5,000 max loss that is **one lot** - even though the capital would have
   bought 31.
6. `/trader-risk`: PASS - 1 of 2 trades used, Rs 4,480 of Rs 15,000 daily risk.
7. `/trader-broker`: paper-filled 65 units at Rs 246.08, charges Rs 30.73.
8. `/trader-alerts --from-plan`: stop Rs 178.34, target Rs 347.69, flat-by 15:15,
   expiry 17 Nov.

That is a complete trade, and the numbers are reproducible: the index data is
public, the seed is printed, and every rupee is arithmetic you can check.

## The tools

```bash
python3 trader-scanner/scan.py daily                     # index setups, all three
python3 trader-scanner/scan.py orb --at 10:15            # opening-range breakouts
python3 trader-news/news.py --hours 12                   # headlines, tagged
python3 trader-entry/entry.py check NIFTY --write        # waiting / triggered + the option
python3 trader-entry/entry.py watch NIFTY --until 14:30  # bar by bar
python3 trader-exit/exit.py plan --stop or-mid --target 1.5R --write
python3 trader-timemachine/timemachine.py run --write    # 10,000 replays + history
python3 trader-budget/size.py --write                    # -> trader/orders.json
python3 trader-risk/check.py                             # PASS / BLOCK per order
python3 trader-broker/ticket.py show                     # the ticket, READY or not
python3 trader-broker/ticket.py preflight                # live checks, sends nothing
python3 trader-broker/ticket.py place --yes              # paper fill, or a real Dhan order
python3 trader-broker/ticket.py book                     # today's Dhan orders
python3 trader-alerts/alerts.py add --from-plan
python3 trader-alerts/alerts.py check                    # rings what hit, once
python3 trader-analyst/analyst.py chain --underlying NIFTY
python3 trader-analyst/analyst.py vix
python3 trader-analyst/analyst.py calendar
python3 trader-journal/journal.py stats
```

Every tool takes `--now "2026-11-09 10:45"` to pin the IST clock and replay a
session, `--json` for machine output, `--offline` for saved data, and
`--trader-dir` for a different state folder.

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline on fixtures cut from real data: NIFTY and SENSEX 5-minute
bars for the 60 sessions to 9 November 2026, India VIX, a slice of the exchange
contract master, and an option chain. They reproduce the numbers in the example.
The 34 Dhan tests inject a fake transport, so the order path - payload fields,
error codes, rate limiting, the live-trading gate, the fill and the flatten - is
tested without a token and without ever touching the network.

## The facts this pack is built on (checked 9 November 2026)

Anything below can change, and SEBI has changed most of it at least once since
2024. `lib/indkit/` is where each one lives, so there is one place to fix it.

**Contracts** (read live from the exchange's own contract list, never hardcoded):
NIFTY lot 65, weekly expiry Tuesday · BANK NIFTY lot 30, monthly, last Tuesday ·
SENSEX lot 20, weekly expiry Thursday · strike steps 50 / 100 / 100 · minimum
index contract value about Rs 15 lakh.

**Session**: pre-open 09:00-09:15, continuous 09:15-15:30 IST. Index options are
European and cash-settled; from 7 December 2026 expiring contracts settle on the
last 30 minutes' VWAP.

**Charges** on one NIFTY options lot (premium Rs 120, one lot = 65 units,
round trip): brokerage Rs 40, STT 0.15% of the sell premium, exchange transaction
charge 0.03503% of premium turnover, SEBI Rs 10 per crore, stamp 0.003% on the
buy, GST 18% on brokerage + exchange + SEBI. That is Rs 45-100 for a round trip,
versus a premium of Rs 7,800 - roughly 1%, which is why `min_premium` exists.

**Rules that shape the tools**:
- 100% upfront premium for buyers (1 Feb 2025) - no leverage on premium.
- One weekly expiry per exchange: NIFTY on NSE, SENSEX on BSE (1 Sep 2025).
- +2% extreme loss margin on short options expiring that day, and no
  calendar-spread benefit on expiry day (20 Nov 2024).
- Intraday monitoring of index position limits, delta-based (1 Oct 2025).
- Retail algo order placement needs a registered app and a static IP
  (1 Apr 2026). Dhan's API does let a retail account place orders from a
  whitelisted IP, which is what `/trader-broker` uses - off by default.
- STT from 1 Apr 2026: 0.15% on option sale premium, 0.15% of intrinsic value on
  exercise. Letting an ITM option expire into settlement is usually a bad deal.

## Going live on Dhan (optional, off by default)

Everything above works with no broker account at all. If you have one, Dhan is
the shortest path from this pack to a real fill: the same token that gives you a
real option chain also places orders, and the API is free once the Data API add-on
is paid for.

```bash
export DHAN_CLIENT_ID="1000xxxxxx"          # never in config.json, never in the chat
export DHAN_ACCESS_TOKEN="eyJ..."           # DhanHQ v2 token, about 30 days
```

With only the token set, the pack quietly gets better: the chain is real (LTP,
open interest, IV, greeks, bid/ask), so PCR, max pain and OI walls stop being
refused, and every premium in `/trader-entry`, `/trader-exit` and
`/trader-analyst` is a quote instead of a model price.

Placing orders takes three switches you have to set on purpose:

```json
{ "paper": false, "live_broker": "dhan", "live_trading_enabled": true }
```

and even then nothing is sent without all four of: a `/trader-risk` PASS for that
exact order today, your explicit yes, a passing `ticket.py preflight` (real quote
within your slippage cap, cash for the premium, market open, a security id for
that contract), and Dhan accepting the order.

```bash
python3 trader-broker/ticket.py preflight      # reads only; sends nothing
python3 trader-broker/ticket.py place --yes    # a real order when live, a paper fill otherwise
python3 trader-broker/ticket.py status         # reconciles against Dhan, cancels orphaned exits
python3 trader-broker/ticket.py flatten --yes  # cancel the exits, sell at market
```

Two things worth knowing before you turn it on. **Dhan has no OCO**: the stop and
the target are two separate orders, so one filling leaves the other working until
`status` or `flatten` cancels it. And **order placement needs a static IP
whitelisted on your Dhan account** - SEBI's algo rule from 1 April 2026 - so a
working token alone is not enough; a rejected order comes back with Dhan's own
words in `omsErrorDescription`.

## Data sources, and how honest they are

| what | source | honest limitation |
| --- | --- | --- |
| index OHLC, India VIX | Yahoo Finance | free, unofficial, can lag, 5-minute bars not ticks |
| lots, expiries, strikes, broker symbols | Kite's public instrument dumps, Upstox master as fallback | a snapshot; refresh after an exchange circular |
| option chain (best) | **Dhan `/v2/optionchain`** | real LTP, OI, IV, greeks, bid/ask and the exchange security id - needs a Dhan Data API subscription and a token in the environment |
| option chain | NSE / BSE endpoints | often blocked from datacentre IPs - your home connection may work |
| option chain (reliable) | your own CSV export | the only chain that is truly yours; `--chain-file` |
| option chain (fallback) | Black-Scholes from spot + India VIX | **labelled MODELLED everywhere**: no OI, no bid/ask, no real smile |
| news | Google News RSS | other people's headlines, with their lag and their errors |
| holidays | NSE/BSE 2026 list; 2027 tentative | expiry dates come from the contract master, which is authoritative |

Every tool says which source it used, in the same breath as the number. A
modelled premium is never presented as a quote.

## Fine print

**Not investment advice.** This is software that reads public data and does
arithmetic. It does not know your situation, your tax position or your
tolerance, and it cannot see the future.

**Options can go to zero.** A bought option can lose 100% of its premium, and in
a fast market a stop-loss order can be skipped entirely. The max loss in these
tools is the loss *at the stop price*, which is a plan, not a guarantee.

**Theta is always working.** An eight-day option loses about Rs 14 a day per
NIFTY unit at the money (Rs 910 a lot in the example). That is the rent on the
position, and it is charged whether or not the index does what you expected.

**Only one skill can place an order.** `/trader-broker`, and only after
`/trader-risk` passed that exact order today, only after you say yes to that
ticket, and only when you have flipped all three live switches yourself. Every
other skill reads and writes files. With `paper: false` but live trading not
fully enabled, it prints the broker request and stops there.

**A live order is your order.** The pack's arithmetic is checked against a real
session, but markets are not. A stop order can be skipped, a fill can be worse
than the quote, and a rejected exit leg leaves a position unprotected - the tool
says so in capitals, and the Dhan app is still the place you fix it.

**Paper first.** `"paper": true` is the default. Turn it off only when the
numbers on your own paper trades are ones you can live with.

**Taxes.** F&O gains are business income, taxed at your slab; the audit
thresholds depend on turnover (for F&O, the sum of absolute profits and losses
plus the premium received on sale of options), and STT is a deductible expense.
None of this is tax advice - talk to an accountant.

**The Time Machine is a simulation.** It draws from the index's own last 60
sessions and reprices the option with a fixed implied vol. It does not model an
IV crush after an RBI decision, a halt, or a headline. Treat its odds as a
reality check, never as a promise.

**The chain is the weakest link.** If your chain is modelled, the premiums,
greeks and stop distances are model output. Check them in your terminal before
you act.

## Files

```
lib/indkit/            the shared toolkit (stdlib only)
  clock.py             IST session clock, NSE holidays, bar grid, entry windows
  instruments.py       contract master: lots, expiries, strikes, broker symbols
  feed.py              index OHLC from Yahoo, normalised to completed IST bars
  bs.py                Black-Scholes price, greeks, implied vol
  chain.py             option chain: Dhan, NSE, BSE, CSV, modelled fallback
  costs.py             the Indian charge stack in rupees
  indicators.py        EMA, RSI, ATR, percentiles
  workflow.py          contract choice, ORB signal, index<->premium translation
  rules.py             SEBI/NSE market structure the risk gate enforces
  store.py             the trader/ folder and its JSON contract
  cli.py               shared flags: --now, --json, --offline, --trader-dir
  brokers/dhan.py      DhanHQ v2: option chain, quotes, funds, real orders
trader-scanner/        SKILL.md, scan.py
trader-news/           SKILL.md, news.py
trader-entry/          SKILL.md, entry.py
trader-exit/           SKILL.md, exit.py
trader-timemachine/    SKILL.md, timemachine.py
trader-budget/         SKILL.md, size.py, config.example.json
trader-risk/           SKILL.md, check.py
trader-broker/         SKILL.md, ticket.py
trader-alerts/         SKILL.md, alerts.py
trader-analyst/        SKILL.md, analyst.py
trader-journal/        SKILL.md, journal.py
tests/                 unittest suite + real-data fixtures
```

MIT. The US original is by [Jake Schincariol](https://opusjake.ai); this
conversion keeps its method, its refusals and its paper-first stance, and points
them at NSE and BSE.
