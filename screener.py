"""
The 4 screening strategies. Each check function evaluates ONE symbol at
ONE bar index `i` — this is the key design choice: the LIVE screener calls
it at the latest bar, and the BACKTESTER calls the exact same function at
every historical bar. So what you backtest is literally what you trade.
"""

import pandas as pd
import config
from indicators import enrich


# ---------------------------------------------------------------- helpers
def _liquid_enough(df: pd.DataFrame, i: int, min_value: float) -> bool:
    v = df["avg_value"].iloc[i]
    return pd.notna(v) and v >= min_value


def _crossed_above_recently(a: pd.Series, b: pd.Series, i: int, lookback: int) -> bool:
    """True if series a crossed above series b within the last `lookback` bars ending at i."""
    start = max(1, i - lookback + 1)
    for j in range(start, i + 1):
        if a.iloc[j - 1] <= b.iloc[j - 1] and a.iloc[j] > b.iloc[j]:
            return True
    return False


# ---------------------------------------------------------------- strategies
def check_trending(df: pd.DataFrame, i: int, p: dict) -> bool:
    row = df.iloc[i]
    if pd.isna(row["ema200"]) or pd.isna(row["adx"]):
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False
    if p["require_ema_stack"]:
        if not (row["close"] > row["ema20"] > row["ema50"] > row["ema200"]):
            return False
    if row["adx"] < p["adx_min"]:
        return False
    if not (p["rsi_min"] <= row["rsi"] <= p["rsi_max"]):
        return False
    return True


def check_early_uptrend(df: pd.DataFrame, i: int, p: dict) -> bool:
    row = df.iloc[i]
    if pd.isna(row["ema200"]) or pd.isna(row["vol_ratio"]):
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False
    if not _crossed_above_recently(df["ema20"], df["ema50"], i, p["cross_lookback"]):
        return False
    if p["require_above_ema200"] and row["close"] <= row["ema200"]:
        return False
    if row["vol_ratio"] < p["volume_ratio_min"]:
        return False
    if row["rsi"] < p["rsi_min"]:
        return False
    return True


def check_reversal(df: pd.DataFrame, i: int, p: dict) -> bool:
    row = df.iloc[i]
    if pd.isna(row["rsi"]) or pd.isna(row["macd"]):
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False
    # momentum WAS weak...
    start = max(0, i - p["rsi_lookback"])
    if df["rsi"].iloc[start:i].min() > p["rsi_was_below"]:
        return False
    # ...and is NOW strong
    if row["rsi"] < p["rsi_now_above"]:
        return False
    if p["require_macd_cross"]:
        # cross must have happened recently AND still be intact now
        if row["macd"] <= row["macd_signal"]:
            return False
        if not _crossed_above_recently(df["macd"], df["macd_signal"], i, p["macd_cross_lookback"]):
            return False
    if p["require_close_above_ema20"] and row["close"] <= row["ema20"]:
        return False
    return True


def check_gaining_momentum(df: pd.DataFrame, i: int, p: dict) -> bool:
    row = df.iloc[i]
    roc_col = f"roc{p['roc_period']}"
    if pd.isna(row.get(roc_col)) or pd.isna(row["vol_ratio"]):
        return False
    if row["close"] < p["min_price"]:
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False
    if row["vol_ratio"] < p["volume_ratio_min"]:
        return False
    if row[roc_col] < p["roc_min"]:
        return False
    # MACD histogram rising N bars in a row = momentum accelerating
    n = p["macd_hist_rising_bars"]
    if i < n:
        return False
    hist = df["macd_hist"].iloc[i - n:i + 1]
    if not all(hist.iloc[k] > hist.iloc[k - 1] for k in range(1, len(hist))):
        return False
    return True


CHECKS = {
    "trending": check_trending,
    "early_uptrend": check_early_uptrend,
    "reversal": check_reversal,
    "gaining_momentum": check_gaining_momentum,
}


# ---------------------------------------------------------------- live scan
def scan(data: dict[str, pd.DataFrame], strategies: dict | None = None) -> dict[str, list[dict]]:
    """
    Run all enabled strategies on the LATEST bar of every symbol.
    Returns {strategy_name: [ {symbol, close, rsi, adx, vol_ratio}, ... ]}
    """
    strategies = strategies or config.STRATEGIES
    hits: dict[str, list[dict]] = {name: [] for name in strategies}

    for symbol, raw in data.items():
        df = enrich(raw)
        i = len(df) - 1
        for name, params in strategies.items():
            if not params.get("enabled", True):
                continue
            try:
                if CHECKS[name](df, i, params):
                    row = df.iloc[i]
                    hits[name].append({
                        "symbol": symbol,
                        "close": round(float(row["close"]), 3),
                        "rsi": round(float(row["rsi"]), 1),
                        "adx": round(float(row["adx"]), 1),
                        "vol_ratio": round(float(row["vol_ratio"]), 2),
                        "roc10": round(float(row["roc10"]), 2),
                    })
            except Exception:
                continue
    # strongest volume signals first within each strategy
    for name in hits:
        hits[name].sort(key=lambda r: r["vol_ratio"], reverse=True)
    return hits
