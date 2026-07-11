"""
Main entry point.

  python main.py            # run one scan now, send Telegram alert
  python main.py --dry-run  # scan but print instead of sending
  python main.py --schedule # run automatically after Bursa close (17:15 MYT) daily
"""

import argparse
import time
from datetime import datetime

import config
from data_fetcher import fetch_market
from screener import scan
from telegram_bot import (send_scan_results, format_scan_results,
                          send_charts, STRATEGY_LABELS)
from charting import make_chart
from indicators import enrich


def build_charts(hits: dict, data: dict) -> dict[str, list[tuple[str, str]]]:
    """Render a 3-month chart for every matched stock.
    Returns {strategy: [(png_path, caption), ...]} preserving hit order
    (already sorted strongest-volume first)."""
    charts: dict[str, list[tuple[str, str]]] = {}
    for strat, rows in hits.items():
        items = []
        for r in rows:
            sym = r["symbol"]
            if sym not in data:
                continue
            df = enrich(data[sym])
            # chart title: plain-ASCII label (matplotlib font has no emoji)
            label = STRATEGY_LABELS.get(strat, strat).split("(")[0]
            label = "".join(ch for ch in label if ord(ch) < 128).strip().upper()
            path = make_chart(df, sym, label, r, bars=config.CHARTS["bars"])
            if path:
                nm = (r.get("name") or "").strip()
                nm_part = f" · {nm[:30]}" if nm else ""
                caption = (f"<b>{sym}</b>{nm_part}  {config.CURRENCY}{r['close']} | RSI {r['rsi']} | "
                           f"ADX {r['adx']} | Vol {r['vol_ratio']}x | "
                           f"ROC10 {r['roc10']}%")
                items.append((path, caption))
        if items:
            charts[strat] = items
    return charts


def run_scan(dry_run: bool = False, trigger: str = "scheduled"):
    scope = f"{config.MARKET_NAME} ({config.UNIVERSE_MODE})" if config.USE_FULL_MARKET else f"{len(config.WATCHLIST)}-stock watchlist"
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Scanning {scope} (trigger: {trigger})...")
    data = fetch_market()
    stocks_screened = len(data)
    print(f"Data OK for {stocks_screened} symbols. Screening...")
    hits = scan(data)

    # attach company names (from the universe scanner data) to every hit
    try:
        from universe import get_name_map
        names = get_name_map()
        for rows in hits.values():
            for r in rows:
                r["name"] = names.get(str(r["symbol"]), "")
    except Exception:
        pass

    total = sum(len(v) for v in hits.values())
    scan_date = datetime.now().strftime("%d %b %Y %H:%M")

    # mark which hits are NEW (not alerted in the last 7 days), then log them
    from signal_log import mark_new_in_hits, append_signals, load_log
    log_was_empty = load_log().empty
    hits = mark_new_in_hits(hits)
    n_logged, n_new = append_signals(hits)
    print(f"Signal log: {n_logged} logged, {n_new} new (log empty before scan: {log_was_empty})")

    if dry_run:
        print(format_scan_results(hits, scan_date, stocks_screened)
              .replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", ""))
        if config.CHARTS["enabled"] and total:
            charts = build_charts(hits, data)
            n = sum(len(v) for v in charts.values())
            print(f"[dry-run] rendered {n} charts into ./charts/ (not sent)")
    else:
        ok = send_scan_results(hits, scan_date, stocks_screened)
        print(f"Telegram sent: {ok} ({stocks_screened} screened, {total} total hits)")
        if log_was_empty and total > 0:
            from telegram_bot import send_message
            send_message(
                "⚠️ <b>Signal log was empty at scan time.</b>\n"
                "If this is not the first-ever run, the log isn't persisting "
                "between runs — every stock will show as 🆕 and the "
                "'Still valid' list will stay empty.\n"
                "Check: repo Settings → Actions → General → Workflow "
                "permissions = <b>Read and write</b>, and the "
                "'Commit signal log' step in the Actions run log."
            )
        if config.CHARTS["enabled"] and total:
            print("Rendering charts...")
            charts = build_charts(hits, data)
            n = sum(len(v) for v in charts.values())
            albums = send_charts(charts,
                                 pause_seconds=config.CHARTS["album_pause_s"])
            print(f"Charts: {n} rendered, {albums} albums sent")


def schedule_loop(run_at: str = "17:15"):
    """Simple scheduler — runs every weekday at `run_at` local (MYT) time.
    Bursa closes 17:00; data settles a few minutes later."""
    print(f"Scheduler running — will scan weekdays at {run_at}. Ctrl+C to stop.")
    last_run_day = None
    while True:
        now = datetime.now()
        hhmm = now.strftime("%H:%M")
        if now.weekday() < 5 and hhmm >= run_at and last_run_day != now.date():
            run_scan()
            last_run_day = now.date()
        time.sleep(30)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--schedule", action="store_true")
    ap.add_argument("--time", default="17:15", help="daily scan time (HH:MM, MYT)")
    args = ap.parse_args()

    if args.schedule:
        schedule_loop(args.time)
    else:
        run_scan(dry_run=args.dry_run)
