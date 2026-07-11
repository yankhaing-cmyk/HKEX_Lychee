"""
Signal log — the memory of every signal the screener has ever fired.

Each scan appends its matches to signals.csv. On GitHub Actions the workflow
commits this file back to the repo, so history accumulates run after run.

A signal is flagged is_new=True only if that (symbol, strategy) pair has NOT
fired within the previous NEW_WINDOW_DAYS. The weekly review evaluates ONLY
new signals — a stock trending for 3 weeks fires daily, but counting it 15
times would badly skew the stats. "How does a FRESH signal perform?" is the
question that actually evaluates your criteria.
"""

import os
from datetime import datetime, timedelta

import pandas as pd

import config

LOG_FILE = f"signals_{config.MARKET.lower()}.csv"
NEW_WINDOW_DAYS = 7   # a repeat within this window is not a "new" signal

COLUMNS = ["date", "symbol", "strategy", "close", "rsi", "adx",
           "vol_ratio", "roc10", "is_new"]


def load_log() -> pd.DataFrame:
    if os.path.exists(LOG_FILE):
        df = pd.read_csv(LOG_FILE)
        df["date"] = pd.to_datetime(df["date"])
        return df
    return pd.DataFrame(columns=COLUMNS)


def append_signals(hits: dict[str, list[dict]], scan_dt: datetime | None = None) -> tuple[int, int]:
    """Append today's hits to the log. Returns (n_logged, n_new).
    Won't double-log the same (date, symbol, strategy)."""
    scan_dt = scan_dt or datetime.now()
    scan_date = pd.Timestamp(scan_dt.date())
    log = load_log()

    cutoff = scan_date - timedelta(days=NEW_WINDOW_DAYS)
    recent = log[log["date"] >= cutoff]
    recent_pairs = set(zip(recent["symbol"], recent["strategy"]))
    today_pairs = set(zip(
        log.loc[log["date"] == scan_date, "symbol"],
        log.loc[log["date"] == scan_date, "strategy"],
    ))

    rows, n_new = [], 0
    for strat, items in hits.items():
        for r in items:
            key = (r["symbol"], strat)
            if key in today_pairs:
                continue  # already logged this scan-day (e.g. /run twice)
            is_new = key not in recent_pairs
            n_new += int(is_new)
            rows.append({
                "date": scan_date, "symbol": r["symbol"], "strategy": strat,
                "close": r["close"], "rsi": r["rsi"], "adx": r["adx"],
                "vol_ratio": r["vol_ratio"], "roc10": r["roc10"],
                "is_new": is_new,
            })
            today_pairs.add(key)

    if rows:
        log = pd.concat([log, pd.DataFrame(rows)], ignore_index=True)
        log.to_csv(LOG_FILE, index=False)
    return len(rows), n_new


def mark_new_in_hits(hits: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Annotate each hit dict with 'is_new' (without writing anything),
    so the alert message can show a NEW badge. Uses the log state BEFORE
    today's append."""
    log = load_log()
    cutoff = pd.Timestamp(datetime.now().date()) - timedelta(days=NEW_WINDOW_DAYS)
    recent = log[(log["date"] >= cutoff) &
                 (log["date"] < pd.Timestamp(datetime.now().date()))]
    recent_pairs = set(zip(recent["symbol"], recent["strategy"]))
    for strat, items in hits.items():
        for r in items:
            r["is_new"] = (r["symbol"], strat) not in recent_pairs
    return hits
