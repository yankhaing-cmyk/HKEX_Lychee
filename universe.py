"""
Fetch the FULL Bursa Malaysia stock universe from TradingView's scanner API
(the same backend that powers tradingview.com/screener).

Two jobs:
  1. get_universe()  -> list of ALL listed MYX stocks (warrants/ETFs excluded),
                        pre-filtered server-side by price & traded value so you
                        don't waste time downloading history for dead counters.
  2. Results are cached to universe_cache.csv for the day, so repeated runs
     (screen, then backtest, then optimize) don't re-hit the API.

The scanner can also return live indicator values (RSI, ADX, EMAs, volume) in
the SAME request — we use that as a cheap first-pass filter before downloading
full history for precise screening + backtesting.
"""

import json
import logging
import os
import time
from datetime import date

import pandas as pd
import requests

import config

log = logging.getLogger("universe")

SCANNER_URL = f"https://scanner.tradingview.com/{config.SCANNER_SLUG}/scan"
CACHE_FILE = f"universe_cache_{config.MARKET.lower()}.csv"

# Columns we ask the scanner for (current values, straight from TradingView)
COLUMNS = [
    "name",                      # symbol e.g. MAYBANK / 700
    "description",               # company name
    "close",
    "volume",
    "average_volume_10d_calc",
    "market_cap_basic",          # market capitalisation
    "RSI",                       # RSI(14)
    "ADX",                       # ADX(14)
    "EMA20", "EMA50", "EMA200",
    "MACD.macd", "MACD.signal",
    "change",                    # % change today
]


def _scan_request(min_price: float, min_avg_value: float, max_rows: int = 3000) -> list[dict]:
    """One POST to the scanner returns every matching stock on the exchange.

    universe_mode == "all"      -> full listing (optionally price/value floors)
    universe_mode == "top_mcap" -> the top N stocks by market cap, nothing else
    """
    filters = [
        {"left": "type", "operation": "equal", "right": "stock"},
        # exclude preferred shares / depository receipts; keep common stock
        {"left": "subtype", "operation": "in_range", "right": ["common", ""]},
        {"left": "exchange", "operation": "equal", "right": config.EXCHANGE},
    ]

    if config.UNIVERSE_MODE == "top_mcap":
        # Top-N by market cap: sort descending on cap, cut at N. No other floors —
        # the cap ranking itself is the filter.
        sort = {"sortBy": "market_cap_basic", "sortOrder": "desc"}
        row_range = [0, config.UNIVERSE_TOP_N]
    else:
        # Full-market mode: only add the floors if they're actually set (>0).
        if min_price and min_price > 0:
            filters.append({"left": "close", "operation": "greater", "right": min_price})
        if min_avg_value and min_avg_value > 0:
            filters.append({"left": "Value.Traded", "operation": "greater", "right": min_avg_value})
        sort = {"sortBy": "Value.Traded", "sortOrder": "desc"}
        row_range = [0, max_rows]

    payload = {
        "filter": filters,
        "options": {"lang": "en"},
        "columns": COLUMNS,
        "sort": sort,
        "range": row_range,
    }
    r = requests.post(SCANNER_URL, json=payload, timeout=30,
                      headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    rows = []
    for item in data.get("data", []):
        d = dict(zip(COLUMNS, item["d"]))
        d["tv_symbol"] = item["s"]            # e.g. "MYX:MAYBANK"
        d["symbol"] = d.pop("name")
        rows.append(d)
    log.info(f"scanner returned {len(rows)} stocks (totalCount={data.get('totalCount')})")
    return rows


def get_universe(force_refresh: bool = False) -> pd.DataFrame:
    """Full Bursa universe as a DataFrame, cached per day.

    Columns include current close, volume, RSI, ADX, EMAs, MACD — enough for
    a rough first-pass filter before downloading full history."""
    u = config.UNIVERSE
    if not force_refresh and os.path.exists(CACHE_FILE):
        cached = pd.read_csv(CACHE_FILE)
        if not cached.empty and cached["cache_date"].iloc[0] == str(date.today()):
            log.info(f"using cached universe ({len(cached)} stocks)")
            cached["symbol"] = cached["symbol"].astype(str)
            _populate_name_map(cached)
            return cached

    rows = _scan_request(u["min_price"], u["min_avg_value_myr"], u["max_stocks"])
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype(str)
    df["cache_date"] = str(date.today())
    df.to_csv(CACHE_FILE, index=False)
    _populate_name_map(df)
    return df


# ---------------------------------------------------------------- company names
_NAME_MAP: dict[str, str] = {}


def _populate_name_map(df: pd.DataFrame):
    global _NAME_MAP
    if "description" in df.columns:
        _NAME_MAP = dict(zip(df["symbol"].astype(str),
                             df["description"].fillna("").astype(str)))


def get_name_map() -> dict[str, str]:
    """symbol -> company name. Loads from today's universe cache if this
    process hasn't populated the map yet (e.g. standalone review runs)."""
    global _NAME_MAP
    if not _NAME_MAP and os.path.exists(CACHE_FILE):
        try:
            _populate_name_map(pd.read_csv(CACHE_FILE))
        except Exception:
            pass
    return _NAME_MAP


def prefilter_candidates(universe: pd.DataFrame) -> list[str]:
    """
    Cheap first pass using the scanner's OWN current indicator values.
    Any stock that could plausibly match ANY enabled strategy survives; the
    precise (and backtestable) checks run later on full history.

    This typically cuts ~900 stocks down to 100-300, making the full-market
    scan finish in a few minutes instead of an hour.
    """
    if universe.empty:
        return []
    df = universe.copy()
    for col in ["RSI", "ADX", "EMA20", "EMA50", "EMA200", "close", "volume",
                "average_volume_10d_calc"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    vol_ratio = df["volume"] / df["average_volume_10d_calc"].replace(0, pd.NA)

    plausible = pd.Series(False, index=df.index)
    S = config.STRATEGIES

    if S["trending"]["enabled"]:
        plausible |= (
            (df["close"] > df["EMA20"]) & (df["EMA20"] > df["EMA50"])
            & (df["ADX"] >= S["trending"]["adx_min"] - 5)          # loose margin
            & (df["RSI"] >= S["trending"]["rsi_min"] - 5)
        )
    if S["early_uptrend"]["enabled"]:
        # near/just-after an EMA20-50 cross: EMAs within a few % of each other
        plausible |= (
            (df["EMA20"] >= df["EMA50"] * 0.98)
            & (df["close"] > df["EMA200"] * 0.97)
            & (df["RSI"] >= S["early_uptrend"]["rsi_min"] - 10)
        )
    if S["reversal"]["enabled"]:
        plausible |= (
            (df["RSI"] >= S["reversal"]["rsi_now_above"] - 5)
            & (df["MACD.macd"] >= df["MACD.signal"] * 0.9)
        )
    if S["gaining_momentum"]["enabled"]:
        plausible |= (
            (vol_ratio >= S["gaining_momentum"]["volume_ratio_min"] * 0.7)
            & (df["change"] > 0)
        )

    out = df.loc[plausible.fillna(False), "symbol"].dropna().unique().tolist()
    log.info(f"prefilter: {len(df)} -> {len(out)} candidates for deep scan")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uni = get_universe(force_refresh=True)
    print(f"{len(uni)} stocks in universe. Top 20 by traded value:")
    print(uni[["symbol", "description", "close", "RSI", "ADX"]].head(20).to_string(index=False))
    cands = prefilter_candidates(uni)
    print(f"\n{len(cands)} candidates pass the rough pre-filter.")
