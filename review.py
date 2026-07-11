"""
Weekly performance review — the feedback loop.

Reads signals.csv, fetches what actually happened to each NEW signal after it
fired, and reports per-strategy performance to Telegram:

    win rate and average return at +5, +10 and +20 trading days

This is how you evaluate whether the screening criteria are actually working
on LIVE signals, not just in backtests. If a strategy's live numbers diverge
badly from its backtest numbers, the backtest was overfit — tighten or retire
that strategy.

Run:  python review.py            (sends to Telegram)
      python review.py --dry-run  (prints only)
"""

import argparse
from datetime import datetime, timedelta

import pandas as pd

import config
from signal_log import load_log
from data_fetcher import fetch_many
from telegram_bot import send_message, STRATEGY_LABELS

HORIZONS = [5, 10, 20]          # trading days after the signal
LOOKBACK_WEEKS = 8              # evaluate signals from the last N weeks


def forward_returns(sig_date: pd.Timestamp, sig_close: float,
                    df: pd.DataFrame) -> dict:
    """Return {h: pct_return} for each horizon with enough data, plus the
    latest return for still-open signals."""
    idx = df.index.searchsorted(sig_date)
    # align to the actual signal bar (or nearest following bar)
    if idx >= len(df):
        return {}
    out = {}
    for h in HORIZONS:
        j = idx + h
        if j < len(df):
            out[h] = (float(df["close"].iloc[j]) / sig_close - 1) * 100
    out["latest"] = (float(df["close"].iloc[-1]) / sig_close - 1) * 100
    out["days_elapsed"] = len(df) - 1 - idx
    return out


def build_review(log: pd.DataFrame, data: dict[str, pd.DataFrame]) -> str:
    lines = [f"<b>📋 {config.MARKET_NAME} Weekly Signal Review — {datetime.now():%d %b %Y}</b>",
             f"<i>New signals from the last {LOOKBACK_WEEKS} weeks, "
             f"evaluated at +5/+10/+20 trading days</i>", ""]

    for strat in log["strategy"].unique():
        s = log[log["strategy"] == strat]
        rets = {h: [] for h in HORIZONS}
        open_rets, evaluated = [], 0
        best = ("-", -1e9)
        worst = ("-", 1e9)

        for _, row in s.iterrows():
            df = data.get(row["symbol"])
            if df is None:
                continue
            fr = forward_returns(row["date"], row["close"], df)
            if not fr:
                continue
            evaluated += 1
            got_any_horizon = False
            for h in HORIZONS:
                if h in fr:
                    rets[h].append(fr[h])
                    got_any_horizon = True
            if not got_any_horizon:
                open_rets.append(fr["latest"])
            r10 = fr.get(10, fr["latest"])
            if r10 > best[1]:
                best = (row["symbol"], r10)
            if r10 < worst[1]:
                worst = (row["symbol"], r10)

        if evaluated == 0:
            continue
        label = STRATEGY_LABELS.get(strat, strat).split("(")[0].strip()
        lines.append(f"<b>{label}</b> — {evaluated} new signals")
        for h in HORIZONS:
            if rets[h]:
                arr = pd.Series(rets[h])
                win = 100 * (arr > 0).mean()
                lines.append(f"  +{h}d: {win:.0f}% win | avg {arr.mean():+.1f}% "
                             f"| median {arr.median():+.1f}%  (n={len(arr)})")
        if open_rets:
            arr = pd.Series(open_rets)
            lines.append(f"  open (too recent): avg {arr.mean():+.1f}% (n={len(arr)})")
        lines.append(f"  best: {best[0]} {best[1]:+.1f}% | worst: {worst[0]} {worst[1]:+.1f}%")
        lines.append("")

    if len(lines) <= 3:
        lines.append("No evaluable signals yet — the log needs a few more scan days.")
    lines.append("<i>Live performance vs backtest is the true test of the criteria. "
                 "Not financial advice.</i>")
    return "\n".join(lines)


def run_review(dry_run: bool = False):
    log = load_log()
    if log.empty:
        msg = "📋 Weekly review: no signals logged yet."
        print(msg) if dry_run else send_message(msg)
        return

    cutoff = pd.Timestamp(datetime.now().date()) - timedelta(weeks=LOOKBACK_WEEKS)
    log = log[(log["date"] >= cutoff) & (log["is_new"] == True)]  # noqa: E712
    if log.empty:
        msg = "📋 Weekly review: no NEW signals in the review window yet."
        print(msg) if dry_run else send_message(msg)
        return

    symbols = sorted(log["symbol"].unique())
    print(f"Reviewing {len(log)} new signals across {len(symbols)} symbols...")
    data = fetch_many(symbols, max_workers=config.UNIVERSE["max_workers"])

    report = build_review(log, data)
    if dry_run:
        print(report.replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", ""))
    else:
        # chunk to Telegram's limit
        msg = report
        while msg:
            chunk, msg = msg[:4000], msg[4000:]
            send_message(chunk)
        print("Review sent.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run_review(dry_run=args.dry_run)
