"""
Technical indicators in pure pandas/numpy — no TA-Lib install headaches.
Every function takes an OHLCV DataFrame and returns the SAME DataFrame
with new columns appended, so you can chain them.
"""

import numpy as np
import pandas as pd


def add_emas(df: pd.DataFrame, periods=(20, 50, 200)) -> pd.DataFrame:
    for p in periods:
        df[f"ema{p}"] = df["close"].ewm(span=p, adjust=False).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def add_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


def add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=1 / period, adjust=False).mean()
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    df["atr"] = atr
    return df


def add_volume_metrics(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    df["vol_avg20"] = df["volume"].rolling(period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_avg20"]
    df["avg_value"] = (df["close"] * df["volume"]).rolling(period).mean()  # MYR traded/day
    return df


def add_roc(df: pd.DataFrame, period: int = 10) -> pd.DataFrame:
    df[f"roc{period}"] = df["close"].pct_change(period) * 100
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Add the full indicator set used by all strategies."""
    df = df.copy()
    df = add_emas(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_adx(df)
    df = add_volume_metrics(df)
    df = add_roc(df, 10)
    return df
