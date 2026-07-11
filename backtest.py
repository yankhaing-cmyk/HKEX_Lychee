"""
Backtester — replays every strategy over history using the EXACT same
check functions as the live screener, so results reflect what you'd
actually have been alerted to.

For every historical signal:
  entry  = next bar's OPEN (realistic: you get the alert after close)
  exit   = stop-loss / take-profit intrabar, else close after hold_days
  costs  = commission_pct charged both sides

Outputs per strategy (train AND test period separately):
  trades, win rate, avg return, median return, expectancy,
  profit factor, max single-trade drawdown, avg hold days

Usage:
  python backtest.py                 # backtest with current config
  python backtest.py --optimize      # grid-search example on 'trending'
"""

import argparse
import copy
import itertools
import pandas as pd

import config
from indicators import enrich
from screener import CHECKS
from data_fetcher import fetch_market, fetch_many, fetch_watchlist


# ---------------------------------------------------------------- trade sim
def simulate_trade(df: pd.DataFrame, signal_i: int, bt: dict) -> dict | None:
    """Enter at next open after the signal bar; exit on SL/TP/timeout."""
    entry_i = signal_i + 1
    if entry_i >= len(df):
        return None
    entry = float(df["open"].iloc[entry_i])
    if entry <= 0:
        return None

    sl_price = entry * (1 + bt["stop_loss_pct"] / 100)
    tp_price = entry * (1 + bt["take_profit_pct"] / 100)

    exit_price, exit_i, reason = None, None, "timeout"
    last = min(entry_i + bt["hold_days"], len(df) - 1)

    for j in range(entry_i, last + 1):
        lo, hi = float(df["low"].iloc[j]), float(df["high"].iloc[j])
        # conservative: if both hit in one bar, assume stop hit first
        if lo <= sl_price:
            exit_price, exit_i, reason = sl_price, j, "stop"
            break
        if hi >= tp_price:
            exit_price, exit_i, reason = tp_price, j, "target"
            break
    if exit_price is None:
        exit_price, exit_i = float(df["close"].iloc[last]), last

    gross = (exit_price / entry - 1) * 100
    net = gross - 2 * bt["commission_pct"]
    return {
        "entry_date": df.index[entry_i],
        "exit_date": df.index[exit_i],
        "entry": entry,
        "exit": exit_price,
        "ret_pct": net,
        "hold_days": exit_i - entry_i,
        "reason": reason,
    }


# ---------------------------------------------------------------- backtest core
def backtest(data: dict[str, pd.DataFrame],
             strategies: dict | None = None,
             bt: dict | None = None) -> dict[str, pd.DataFrame]:
    """Returns {strategy: DataFrame of trades}."""
    strategies = strategies or config.STRATEGIES
    bt = bt or config.BACKTEST
    all_trades: dict[str, list] = {s: [] for s in strategies}

    for symbol, raw in data.items():
        df = enrich(raw)
        n = len(df)
        for name, params in strategies.items():
            if not params.get("enabled", True):
                continue
            check = CHECKS[name]
            cooldown_until = -1
            for i in range(220, n - 1):        # need EMA200 warm-up
                if i <= cooldown_until:
                    continue                    # don't re-enter while in a trade
                try:
                    if check(df, i, params):
                        tr = simulate_trade(df, i, bt)
                        if tr:
                            tr["symbol"] = symbol
                            tr["strategy"] = name
                            all_trades[name].append(tr)
                            cooldown_until = i + tr["hold_days"] + 1
                except Exception:
                    continue

    return {s: pd.DataFrame(t) for s, t in all_trades.items()}


# ---------------------------------------------------------------- stats
def summarize(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {"trades": 0}
    r = trades["ret_pct"]
    wins, losses = r[r > 0], r[r <= 0]
    gross_win = wins.sum()
    gross_loss = abs(losses.sum())
    return {
        "trades": len(r),
        "win_rate_%": round(100 * len(wins) / len(r), 1),
        "avg_ret_%": round(r.mean(), 2),
        "median_ret_%": round(r.median(), 2),
        "best_%": round(r.max(), 2),
        "worst_%": round(r.min(), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "expectancy_%": round(r.mean(), 2),
        "avg_hold_days": round(trades["hold_days"].mean(), 1),
    }


def train_test_report(trades_by_strat: dict[str, pd.DataFrame], split: float):
    """Split each strategy's trades chronologically; report both halves.
    If a strategy only works in-sample and dies out-of-sample, it's overfit."""
    rows = []
    for strat, tr in trades_by_strat.items():
        if tr is None or tr.empty:
            rows.append({"strategy": strat, "period": "-", "trades": 0})
            continue
        tr = tr.sort_values("entry_date").reset_index(drop=True)
        cut = int(len(tr) * split)
        for period, part in [("TRAIN", tr.iloc[:cut]), ("TEST", tr.iloc[cut:])]:
            s = summarize(part)
            s.update({"strategy": strat, "period": period})
            rows.append(s)
    rep = pd.DataFrame(rows)
    cols = ["strategy", "period", "trades", "win_rate_%", "avg_ret_%",
            "median_ret_%", "profit_factor", "best_%", "worst_%", "avg_hold_days"]
    return rep.reindex(columns=[c for c in cols if c in rep.columns])


# ---------------------------------------------------------------- optimizer
def optimize_trending(data: dict[str, pd.DataFrame]):
    """Example grid search on the 'trending' strategy. Copy this pattern for
    the other strategies. NOTE: judge candidates by TEST performance, not TRAIN,
    otherwise you are just curve-fitting."""
    grid = {
        "adx_min": [20, 25, 30],
        "rsi_min": [45, 50, 55],
        "rsi_max": [70, 75, 80],
    }
    keys = list(grid)
    results = []
    for combo in itertools.product(*grid.values()):
        strat = copy.deepcopy(config.STRATEGIES)
        for s in strat.values():
            s["enabled"] = False
        strat["trending"] = {**config.STRATEGIES["trending"], "enabled": True,
                             **dict(zip(keys, combo))}
        trades = backtest(data, strategies=strat)["trending"]
        if trades.empty:
            continue
        trades = trades.sort_values("entry_date").reset_index(drop=True)
        cut = int(len(trades) * config.BACKTEST["train_test_split"])
        test_stats = summarize(trades.iloc[cut:])
        results.append({**dict(zip(keys, combo)), **test_stats})
    res = pd.DataFrame(results).sort_values("avg_ret_%", ascending=False)
    print("\n=== Optimization results (ranked by OUT-OF-SAMPLE avg return) ===")
    print(res.head(15).to_string(index=False))
    return res


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--optimize", action="store_true", help="grid-search the trending strategy")
    ap.add_argument("--top", type=int, default=0,
                    help="backtest only the N most liquid stocks (0 = all). "
                         "Full market = ~900 downloads; --top 300 is a good balance.")
    args = ap.parse_args()

    # IMPORTANT: for backtesting we do NOT use the live pre-filter — it selects
    # stocks based on TODAY's indicators, which would leak future information
    # into historical results (lookahead bias). We take the whole universe
    # (or the N most liquid), download full history, and let the strategy
    # checks decide at every historical bar.
    if config.USE_FULL_MARKET:
        try:
            from universe import get_universe
            uni = get_universe()
            symbols = uni["symbol"].dropna().unique().tolist()
            if args.top > 0:
                symbols = symbols[:args.top]   # universe is sorted by traded value
            print(f"Backtesting {len(symbols)} stocks from the full Bursa universe...")
            data = fetch_many(symbols, max_workers=config.UNIVERSE["max_workers"])
        except Exception as e:
            print(f"Universe fetch failed ({e}); using WATCHLIST")
            data = fetch_watchlist()
    else:
        data = fetch_watchlist()
    print(f"Got data for {len(data)} symbols.\n")

    if args.optimize:
        optimize_trending(data)
    else:
        trades = backtest(data)
        report = train_test_report(trades, config.BACKTEST["train_test_split"])
        print("=== Backtest report (TRAIN = older data, TEST = recent unseen data) ===")
        print(report.to_string(index=False))
        print("\nRule of thumb: only trust a strategy whose TEST numbers hold up.")
        # save trade logs for inspection
        for strat, tr in trades.items():
            if not tr.empty:
                tr.to_csv(f"trades_{strat}.csv", index=False)
                print(f"saved trades_{strat}.csv ({len(tr)} trades)")
