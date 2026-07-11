# Bursa Malaysia Momentum Screener

Screens **the entire Bursa Malaysia market** (~900+ listed stocks) daily using
TradingView data, alerts you on Telegram, and backtests the exact same criteria
so you can tune the strategy before risking money.

## How full-market scanning works

1. **Universe pull** — one request to TradingView's scanner API returns every
   listed MYX stock (warrants/ETFs excluded), filtered server-side by minimum
   price and daily traded value (`UNIVERSE` in `config.py`). Cached daily.
2. **Cheap pre-filter** — the scanner also returns current RSI/ADX/EMA/MACD
   values, so stocks with zero chance of matching any strategy are dropped
   before any downloads. Typically ~900 → 100–300 candidates.
3. **Deep scan** — full price history is downloaded for the survivors and the
   precise strategy checks run locally (the same checks the backtester uses).

Set `USE_FULL_MARKET = False` in `config.py` to use the manual watchlist instead.
If the scanner API is ever unreachable, it falls back to the watchlist automatically.

## The 4 strategies

| Strategy | What it catches | Core rules (all tunable in `config.py`) |
|---|---|---|
| **trending** | Established, healthy uptrend | Close > EMA20 > EMA50 > EMA200, ADX ≥ 25, RSI 50–75 |
| **early_uptrend** | Trend just starting | EMA20 crossed above EMA50 in last 5 bars, above EMA200, volume ≥ 1.2× |
| **reversal** | Weak → strong momentum flip | RSI was < 35 recently, now > 50, fresh MACD bullish cross, back above EMA20 |
| **gaining_momentum** | Momentum ignition | Volume ≥ 1.8× average, ROC(10) ≥ 3%, MACD histogram rising 3 bars straight |

## Install

```bash
pip install pandas numpy requests yfinance
pip install --upgrade git+https://github.com/rongardF/tvdatafeed.git
```
(`tvdatafeed` is an unofficial TradingView library — the git install is the maintained fork.
If TradingView blocks anonymous access, the code automatically falls back to Yahoo Finance.)

## Setup Telegram (2 minutes)

1. Telegram → search **@BotFather** → `/newbot` → copy the **token**
2. Search **@userinfobot** → it replies with your **chat id**
3. Paste both into `config.py`
4. **Send your new bot any message once** (required before it can message you)

## Run

```bash
python main.py --dry-run     # scan now, print to console
python main.py               # scan now, send Telegram alert
python main.py --schedule    # auto-scan weekdays 17:15 MYT after market close
```

## Run automatically on GitHub Actions (free, no PC needed)

Instead of keeping your machine on with `--schedule`, let GitHub run the scan
for you every weekday after Bursa close. The workflow file is already included
at `.github/workflows/daily-screener.yml`.

Setup (5 minutes):

1. Push this project to a GitHub repo.
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
   Add two secrets:
   - `TELEGRAM_BOT_TOKEN` — your BotFather token
   - `TELEGRAM_CHAT_ID` — your chat id
   (`config.py` reads these from the environment automatically, so your tokens
   never live in the code.)
3. Go to the **Actions** tab and enable workflows if prompted.
4. Test it: open the **Daily Bursa Screener** workflow → **Run workflow** button
   → you should get a Telegram message within a couple of minutes.

After that it runs itself at **09:15 UTC (17:15 MYT), Mon–Fri**. Change the time
by editing the `cron:` line in the workflow file.

Notes:
- GitHub's free scheduled runs can be delayed a few minutes up to ~1 hour under
  load — normal, not a bug.
- If a run fails, the Actions tab shows the full log. The most common cause is a
  missing/misspelled secret.

## Run on demand — send `/run` to your bot

Besides the daily schedule, you can trigger a scan any time by sending **`/run`**
to your Telegram bot. This uses a small, free Cloudflare Worker that relays your
command to GitHub Actions — the Worker does no screening itself; GitHub still
runs the Python. Full walkthrough in **`cloudflare-worker/SETUP.md`**.

```
You type /run → Telegram → Cloudflare Worker → GitHub Actions runs main.py → results back to you
```

## Backtest & tune (do this BEFORE trading a change)

```bash
python backtest.py --top 300   # backtest the 300 most liquid stocks (recommended)
python backtest.py             # the ENTIRE market (~900 downloads, slow first run)
python backtest.py --optimize  # grid-search example on the trending strategy
```

**Note on bias:** the backtest deliberately does NOT use the live pre-filter —
that filter looks at *today's* indicators, and selecting historical stocks with
today's data would leak the future into the past (lookahead bias). The backtest
downloads full history for the whole universe and lets the strategy rules decide
at every historical bar, exactly as they would have in real time.

The report splits history: **TRAIN** (older 70%) vs **TEST** (recent 30%).
A strategy is only trustworthy if the TEST numbers hold up — great TRAIN +
bad TEST = you curve-fit the past.

### The tuning loop

1. Edit a parameter in `config.py` (e.g. raise `adx_min` from 25 → 30)
2. `python backtest.py` → compare win rate / profit factor **on TEST**
3. Keep the change only if TEST improves; revert if only TRAIN improved
4. Once satisfied, the live screener automatically uses the same config —
   there is one set of rules shared by screener and backtester by design.

## Honest notes

- **>100% a year is not something any screener can promise.** A backtest that
  shows it is usually overfit, survivorship-biased, or ignoring liquidity/slippage.
  Judge strategies by out-of-sample profit factor, win rate, and worst trade —
  and size positions so the worst case is survivable.
- Backtest fills are approximations (next-day open entry, stop assumed to fill
  at the stop price). Real fills on thin Bursa counters will be worse — the
  `min_avg_value_myr` liquidity filter exists to reduce this gap.
- tvDatafeed is unofficial; if it breaks, the Yahoo fallback keeps you running.
  Extend `YAHOO_MAP` in `data_fetcher.py` with stock codes for full coverage.

## Files

```
config.py        ← all parameters (universe, strategies, Telegram, backtest)
universe.py      ← pulls ALL Bursa stocks from TradingView scanner + pre-filter
data_fetcher.py  ← TradingView history (tvDatafeed) + Yahoo gap-fill, concurrent
indicators.py    ← EMA / RSI / MACD / ADX / ATR / volume / ROC (pure pandas)
screener.py      ← the 4 strategy checks (shared by live scan AND backtest)
telegram_bot.py  ← alert formatting + sending
backtest.py      ← replay, train/test report, grid-search optimizer
main.py          ← run once or schedule daily after market close
requirements.txt ← dependencies (used by GitHub Actions)
.github/workflows/daily-screener.yml ← runs the scan daily on GitHub's servers
.gitignore       ← keeps caches and secrets out of the repo
```
