"""
Candlestick chart generation for matched stocks.

Renders the last ~3 months (63 trading days) as a TradingView-style chart:
  - candlesticks (green up / red down)
  - EMA 20 / 50 / 200 overlays  (EMAs computed on FULL history, then sliced,
    so the lines are correct even though only 3 months are displayed)
  - volume bars color-matched to candles, with 20-day average volume line

Pure matplotlib — no extra dependencies beyond what's in requirements.txt.
"""

import os

import matplotlib
matplotlib.use("Agg")  # headless — required on GitHub Actions
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

import config

UP, DOWN = "#26a69a", "#ef5350"           # tradingview-style green/red
EMA_COLORS = {"ema20": "#2962ff", "ema50": "#ff9800", "ema200": "#9c27b0"}

CHART_DIR = "charts"


def make_chart(df: pd.DataFrame, symbol: str, strategy_label: str,
               stats: dict, bars: int = 63) -> str | None:
    """
    df: ENRICHED dataframe (must contain ema20/ema50/ema200, vol_avg20).
    Returns the saved PNG path, or None on failure.
    """
    try:
        os.makedirs(CHART_DIR, exist_ok=True)
        d = df.tail(bars).copy()
        n = len(d)
        if n < 10:
            return None
        x = np.arange(n)

        fig, (ax, axv) = plt.subplots(
            2, 1, figsize=(10, 6.2), sharex=True,
            gridspec_kw={"height_ratios": [3, 1], "hspace": 0.05},
        )
        fig.patch.set_facecolor("white")

        # ---- candles + volume bars ----
        for i in range(n):
            c = UP if d["close"].iloc[i] >= d["open"].iloc[i] else DOWN
            ax.plot([x[i], x[i]], [d["low"].iloc[i], d["high"].iloc[i]],
                    color=c, lw=0.9, zorder=2)
            body_low = min(d["open"].iloc[i], d["close"].iloc[i])
            body_h = abs(d["close"].iloc[i] - d["open"].iloc[i])
            ax.add_patch(Rectangle((x[i] - 0.36, body_low), 0.72,
                                   max(body_h, 1e-6),
                                   facecolor=c, edgecolor=c, zorder=3))
            axv.bar(x[i], d["volume"].iloc[i] / 1e6, width=0.72,
                    color=c, alpha=0.85)

        # ---- EMA overlays (drawn only where defined; EMA200 may start
        #      mid-chart for newer listings — that's correct behaviour) ----
        for col, label in [("ema20", "EMA 20"), ("ema50", "EMA 50"),
                           ("ema200", "EMA 200")]:
            if col in d and d[col].notna().any():
                ax.plot(x, d[col], color=EMA_COLORS[col], lw=1.4, label=label)

        if "vol_avg20" in d and d["vol_avg20"].notna().any():
            axv.plot(x, d["vol_avg20"] / 1e6, color="#5d4037", lw=1.3,
                     ls="--", label="20-day avg volume")

        # ---- title with company name + the key stats ----
        name = (stats.get("name") or "").strip()
        # matplotlib's default font can't render CJK glyphs; keep ASCII only
        name_ascii = "".join(ch for ch in name if ord(ch) < 128).strip()
        head = f"{symbol}" + (f"  ·  {name_ascii[:45]}" if name_ascii else "")
        title = (f"{head}\n{strategy_label}  |  "
                 f"{config.CURRENCY}{stats.get('close', '?')}   RSI {stats.get('rsi', '?')}   "
                 f"ADX {stats.get('adx', '?')}   Vol {stats.get('vol_ratio', '?')}x   "
                 f"ROC10 {stats.get('roc10', '?')}%")
        ax.set_title(title, fontsize=10.5, loc="left", pad=10, fontweight="bold")

        ax.legend(loc="upper left", fontsize=8, frameon=False)
        axv.legend(loc="upper left", fontsize=8, frameon=False)
        ax.set_ylabel(f"Price ({config.CURRENCY})", fontsize=9)
        axv.set_ylabel("Vol (M)", fontsize=9)

        ticks = x[:: max(1, n // 6)]
        axv.set_xticks(ticks)
        axv.set_xticklabels([d.index[i].strftime("%d %b") for i in ticks],
                            fontsize=8)
        for a in (ax, axv):
            a.grid(alpha=0.25, lw=0.5)
            a.set_axisbelow(True)
            for s in ["top", "right"]:
                a.spines[s].set_visible(False)
        ax.margins(x=0.01)

        path = os.path.join(CHART_DIR, f"{symbol}.png")
        fig.savefig(path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        print(f"chart failed for {symbol}: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return None
