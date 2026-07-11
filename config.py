"""
Central configuration — tune everything here, then re-run the backtest
to see how the change affects win rate / returns BEFORE trading it live.
"""

import os

# ----------------------------------------------------------------------
# TELEGRAM
# ----------------------------------------------------------------------
# Reads from environment variables FIRST (used by GitHub Actions Secrets),
# falling back to the hardcoded value for local runs. NEVER commit real
# tokens — set them as repo Secrets on GitHub instead (see README).
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "PUT_YOUR_CHAT_ID_HERE")

# ----------------------------------------------------------------------
# MARKET SELECTION
# ----------------------------------------------------------------------
# One codebase, multiple markets. The MARKET env var picks which one this
# run screens (each market has its own workflow + Telegram bot + signal log).
MARKET = os.environ.get("MARKET", "MYX").upper()

MARKETS = {
    "MYX": {
        "exchange": "MYX",           # TradingView exchange code
        "scanner_slug": "malaysia",  # scanner.tradingview.com/<slug>/scan
        "currency": "RM",
        "flag": "🇲🇾",
        "name": "Bursa",
        "universe_mode": "all",      # screen every listed stock
    },
    "HKEX": {
        "exchange": "HKEX",
        "scanner_slug": "hongkong",
        "currency": "HK$",
        "flag": "🇭🇰",
        "name": "HKEX",
        "universe_mode": "top_mcap", # only the top N by market cap
        "top_n": 500,
    },
}

_M = MARKETS[MARKET]
EXCHANGE      = _M["exchange"]
SCANNER_SLUG  = _M["scanner_slug"]
CURRENCY      = _M["currency"]
MARKET_FLAG   = _M["flag"]
MARKET_NAME   = _M["name"]
UNIVERSE_MODE = _M["universe_mode"]
UNIVERSE_TOP_N = _M.get("top_n", 0)

# ----------------------------------------------------------------------
# DATA SOURCE
# ----------------------------------------------------------------------
INTERVAL   = "1D"           # daily bars
N_BARS     = 400            # history pulled per symbol (need >= 250 for EMA200)

# ----------------------------------------------------------------------
# UNIVERSE — screen the WHOLE market
# ----------------------------------------------------------------------
# When True, the screener pulls EVERY listed Bursa stock from TradingView's
# scanner (warrants/ETFs excluded), applies the filters below server-side,
# then does a cheap indicator pre-filter before downloading full history.
USE_FULL_MARKET = True

UNIVERSE = {
    "min_price": 0.01,             # effectively no price floor — screen everything
    "min_avg_value_myr": 0,        # no liquidity floor at universe level
                                   # (each STRATEGY still has its own
                                   #  min_avg_value_myr — tune those instead)
    "max_stocks": 3000,            # safety cap on scanner rows
    "use_prefilter": False,        # False = download history for EVERY stock and
                                   #  run the precise checks on all of them.
                                   #  Slower (~10-15 min/scan) but nothing slips
                                   #  under the radar.
                                   #  True = quick indicator pre-filter first
                                   #  (~3-5 min/scan, may miss borderline setups)
    "max_workers": 8,              # parallel history downloads
}

# Fallback watchlist — used when USE_FULL_MARKET = False or the scanner
# API is unreachable.
WATCHLIST = [
    "MAYBANK", "PBBANK", "CIMB", "TENAGA", "PCHEM", "IHH", "PMETAL",
    "TM", "MRDIY", "SIMEPLT", "GAMUDA", "YTLPOWR", "YTL", "AXIATA",
    "CDB", "SUNWAY", "IOICORP", "KLK", "HLBANK", "RHBBANK", "MISC",
    "PETGAS", "PETDAG", "GENTING", "GENM", "MAXIS", "DIALOG", "INARI",
    "VITROX", "GREATEC", "FRONTKN", "UNISEM", "MPI", "D&O", "SKPRES",
    "PENTA", "GTRONIC", "KGB", "HARTA", "TOPGLOV", "KOSSAN", "SUPERMX",
    "AIRPORT", "CAPITALA", "WPRTS", "BIMB", "AMBANK", "MBSB", "EKOVEST",
    "IJM", "KERJAYA", "HIBISCS",
]

# ----------------------------------------------------------------------
# CHARTS — candlestick charts sent to Telegram for every matched stock
# ----------------------------------------------------------------------
CHARTS = {
    "enabled": True,
    "bars": 63,            # ~3 months of trading days per chart
    "album_pause_s": 3.0,  # pause between albums (Telegram rate limits)
}

# ----------------------------------------------------------------------
# STRATEGY PARAMETERS  (used by both screener AND backtester)
# ----------------------------------------------------------------------
STRATEGIES = {
    # 1) Established trend, still healthy
    "trending": {
        "enabled": True,
        "adx_min": 25,              # trend strength
        "rsi_min": 50,
        "rsi_max": 75,              # avoid chasing overbought
        "require_ema_stack": True,  # close > EMA20 > EMA50 > EMA200
        "min_avg_value_myr": 1_000_000,  # liquidity filter (price*volume 20d avg)
    },

    # 2) Trend just starting — EMA20 crossed above EMA50 recently
    "early_uptrend": {
        "enabled": True,
        "cross_lookback": 5,        # cross happened within last N bars
        "require_above_ema200": True,
        "volume_ratio_min": 1.2,    # vol vs 20d average
        "rsi_min": 50,
        "min_avg_value_myr": 1_000_000,
    },

    # 3) Reversal: weak momentum -> strong momentum
    "reversal": {
        "enabled": True,
        "rsi_was_below": 35,        # was oversold/weak within lookback
        "rsi_lookback": 15,
        "rsi_now_above": 50,        # momentum has flipped
        "require_macd_cross": True, # MACD line crossed above signal recently
        "macd_cross_lookback": 15,
        "require_close_above_ema20": True,
        "min_avg_value_myr": 500_000,
    },

    # 4) Stock starting to gain momentum (volume + price acceleration)
    "gaining_momentum": {
        "enabled": True,
        "volume_ratio_min": 1.8,    # today's vol vs 20d average
        "roc_period": 10,
        "roc_min": 3.0,             # % rate of change
        "macd_hist_rising_bars": 3, # MACD histogram rising N bars in a row
        "min_price": 0.15,          # skip ultra-penny noise
        "min_avg_value_myr": 500_000,
    },
}

# ----------------------------------------------------------------------
# BACKTEST SETTINGS
# ----------------------------------------------------------------------
BACKTEST = {
    "hold_days": 20,           # max holding period per trade
    "stop_loss_pct": -7.0,     # exit if down this much
    "take_profit_pct": 15.0,   # exit if up this much
    "commission_pct": 0.15,    # per side (broker + stamp duty + clearing approx)
    "train_test_split": 0.7,   # first 70% of history = train, last 30% = test
}
