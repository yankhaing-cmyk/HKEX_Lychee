"""
Telegram alerts. Setup (one time):
  1. Message @BotFather on Telegram -> /newbot -> copy the token
  2. Message @userinfobot -> copy your chat id
  3. Paste both into config.py
  4. Send your bot ANY message first (Telegram requires this before a bot can message you)
"""

import requests
import config

STRATEGY_LABELS = {
    "trending": "📈 TRENDING (strong established uptrend)",
    "early_uptrend": "🌱 EARLY UPTREND (EMA20 x EMA50 cross)",
    "reversal": "🔄 REVERSAL (weak → strong momentum)",
    "gaining_momentum": "🚀 GAINING MOMENTUM (volume + acceleration)",
}


def send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        return r.ok
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False


def _hit_line(r: dict) -> str:
    nm = (r.get("name") or "").strip()
    nm = f" {nm[:26]}" if nm else ""
    return (f"    • <b>{r['symbol']}</b>{nm}  {config.CURRENCY}{r['close']}"
            f" | RSI {r['rsi']} | ADX {r['adx']}"
            f" | Vol {r['vol_ratio']}x | ROC10 {r['roc10']}%")


def format_scan_results(hits: dict[str, list[dict]], scan_date: str,
                        stocks_screened: int | None = None) -> str:
    lines = [f"<b>{config.MARKET_FLAG} {config.MARKET_NAME} Screener — {scan_date}</b>"]
    total_hits = sum(len(v) for v in hits.values())
    n_new = sum(1 for v in hits.values() for r in v if r.get("is_new"))
    if stocks_screened is not None:
        top = f"📊 Screened <b>{stocks_screened}</b> stocks · <b>{total_hits}</b> matched"
        if any(r.get("is_new") is not None for v in hits.values() for r in v):
            top += f" · <b>{n_new}</b> new"
        lines.append(top)
    lines.append("")
    any_hit = False
    for strat, rows in hits.items():
        if not rows:
            continue
        any_hit = True
        lines.append(f"<b>{STRATEGY_LABELS.get(strat, strat)}</b>")

        # rows without an is_new flag (e.g. log unavailable) -> treat as new
        new_rows = [r for r in rows if r.get("is_new") in (True, None)]
        old_rows = [r for r in rows if r.get("is_new") is False]

        if new_rows:
            lines.append("  🆕 <b>New today:</b>")
            for r in new_rows:
                lines.append(_hit_line(r))
        if old_rows:
            # compact: symbol + short name, wrapped to keep lines readable
            def _short(r):
                nm = (r.get("name") or "").strip().split(" - ")[0]
                return f"{r['symbol']} {nm[:16]}".strip() if nm else str(r["symbol"])
            syms = [_short(r) for r in old_rows]
            lines.append(f"  ♻️ <b>Still valid ({len(syms)}):</b>")
            for i in range(0, len(syms), 4):
                lines.append("    " + " · ".join(syms[i:i + 4]))
        lines.append("")
    if not any_hit:
        lines.append("No stocks matched any strategy today.")
    lines.append("<i>Screening signal only — not financial advice. Always do your own due diligence.</i>")
    return "\n".join(lines)


def send_scan_results(hits: dict[str, list[dict]], scan_date: str,
                      stocks_screened: int | None = None) -> bool:
    msg = format_scan_results(hits, scan_date, stocks_screened)
    # Telegram limit is 4096 chars — split if needed
    ok = True
    while msg:
        chunk, msg = msg[:4000], msg[4000:]
        ok = send_message(chunk) and ok
    return ok


# ---------------------------------------------------------------- photo albums
def send_photo_album(photos: list[tuple[str, str]]) -> bool:
    """
    Send up to 10 photos as ONE Telegram album (sendMediaGroup).
    photos: list of (filepath, caption) tuples, max 10.
    Each photo carries its own caption (visible when tapped/expanded).
    """
    import json as _json
    if not photos:
        return True
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    media, files, handles = [], {}, []
    try:
        for i, (path, caption) in enumerate(photos[:10]):
            key = f"photo{i}"
            fh = open(path, "rb")
            handles.append(fh)
            files[key] = fh
            media.append({
                "type": "photo",
                "media": f"attach://{key}",
                "caption": caption[:1000],
                "parse_mode": "HTML",
            })
        r = requests.post(url, data={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "media": _json.dumps(media),
        }, files=files, timeout=60)
        if not r.ok:
            print(f"sendMediaGroup failed: {r.status_code} {r.text[:200]}")
        return r.ok
    except Exception as e:
        print(f"album send failed: {e}")
        return False
    finally:
        for fh in handles:
            try:
                fh.close()
            except Exception:
                pass


def send_charts(charts_by_strategy: dict[str, list[tuple[str, str]]],
                pause_seconds: float = 3.0) -> int:
    """
    Send all charts, grouped per strategy, in albums of up to 10.
    charts_by_strategy: {strategy_name: [(png_path, caption), ...]}
    Returns number of albums successfully sent.

    Pacing: Telegram rate-limits bots (~20 messages/min per chat, and each
    album counts as several). A few seconds between albums keeps us safe.
    """
    import time as _time
    sent = 0
    for strat, items in charts_by_strategy.items():
        if not items:
            continue
        label = STRATEGY_LABELS.get(strat, strat)
        for start in range(0, len(items), 10):
            batch = items[start:start + 10]
            # prepend the strategy label to the first caption of each album
            first_path, first_cap = batch[0]
            batch = [(first_path, f"<b>{label}</b>\n{first_cap}")] + batch[1:]
            if send_photo_album(batch):
                sent += 1
            _time.sleep(pause_seconds)
    return sent
