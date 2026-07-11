"""
Fetch OHLCV data for Bursa Malaysia stocks.

Primary source : TradingView via tvDatafeed  (pip install tvdatafeed)
Fallback source: Yahoo Finance via yfinance  (symbol + ".KL")

Both return a pandas DataFrame with columns:
    open, high, low, close, volume   (DatetimeIndex, ascending)
"""

import time
import logging
import pandas as pd

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("data")

# ---------------------------------------------------------------- TradingView
_tv = None

def _get_tv():
    """Lazy-init a single TvDatafeed session (anonymous login works for daily data)."""
    global _tv
    if _tv is None:
        from tvDatafeed import TvDatafeed
        _tv = TvDatafeed()  # anonymous; pass username/password for more history
    return _tv


def fetch_tradingview(symbol: str, n_bars: int = None) -> pd.DataFrame | None:
    from tvDatafeed import Interval
    n_bars = n_bars or config.N_BARS
    interval_map = {
        "1D": Interval.in_daily,
        "1W": Interval.in_weekly,
        "4H": Interval.in_4_hour,
        "1H": Interval.in_1_hour,
    }
    try:
        tv = _get_tv()
        df = tv.get_hist(
            symbol=symbol,
            exchange=config.EXCHANGE,
            interval=interval_map.get(config.INTERVAL, Interval.in_daily),
            n_bars=n_bars,
        )
        if df is None or df.empty:
            return None
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    except Exception as e:
        log.warning(f"TradingView fetch failed for {symbol}: {e}")
        return None


# ---------------------------------------------------------------- Yahoo fallback
def fetch_yahoo(symbol: str, n_bars: int = None) -> pd.DataFrame | None:
    """Yahoo symbol mapping differs per exchange:
    - HKEX: TradingView uses bare numbers ("700"); Yahoo wants 4-digit + .HK
      ("0700.HK") — a clean mechanical mapping.
    - Bursa: Yahoo wants numeric stock codes (1155.KL); mapping table below."""
    import yfinance as yf
    n_bars = n_bars or config.N_BARS
    if config.EXCHANGE == "HKEX":
        try:
            yahoo_symbol = f"{int(symbol):04d}.HK"
        except (ValueError, TypeError):
            yahoo_symbol = f"{symbol}.HK"
    else:
        yahoo_symbol = YAHOO_MAP.get(symbol, f"{symbol}.KL")
    try:
        df = yf.download(yahoo_symbol, period="2y", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        return df.sort_index().tail(n_bars)
    except Exception as e:
        log.warning(f"Yahoo fetch failed for {symbol}: {e}")
        return None


# Map TradingView names -> Yahoo stock codes (extend as needed)
YAHOO_MAP = {
    "MAYBANK": "1155.KL", "PBBANK": "1295.KL", "CIMB": "1023.KL",
    "TENAGA": "5347.KL", "PCHEM": "5183.KL", "IHH": "5225.KL",
    "PMETAL": "8869.KL", "TM": "4863.KL", "MRDIY": "5296.KL",
    "GAMUDA": "5398.KL", "AXIATA": "6888.KL", "SUNWAY": "5211.KL",
    "IOICORP": "1961.KL", "KLK": "2445.KL", "HLBANK": "5819.KL",
    "RHBBANK": "1066.KL", "MISC": "3816.KL", "PETGAS": "6033.KL",
    "PETDAG": "5681.KL", "GENTING": "3182.KL", "GENM": "4715.KL",
    "MAXIS": "6012.KL", "DIALOG": "7277.KL", "INARI": "0166.KL",
    "VITROX": "0097.KL", "HARTA": "5168.KL", "TOPGLOV": "7113.KL",
}


# ---------------------------------------------------------------- Public API
def fetch(symbol: str, n_bars: int = None) -> pd.DataFrame | None:
    """Try TradingView first, fall back to Yahoo."""
    df = fetch_tradingview(symbol, n_bars)
    if df is None:
        df = fetch_yahoo(symbol, n_bars)
    return df


def fetch_watchlist(symbols: list[str] | None = None, delay: float = 0.5) -> dict[str, pd.DataFrame]:
    """Fetch a list of symbols sequentially. Returns {symbol: DataFrame}."""
    symbols = symbols or config.WATCHLIST
    out = {}
    for i, sym in enumerate(symbols, 1):
        log.info(f"[{i}/{len(symbols)}] fetching {sym}")
        df = fetch(sym)
        if df is not None and len(df) >= 60:
            out[sym] = df
        else:
            log.warning(f"skipped {sym} (no/insufficient data)")
        time.sleep(delay)  # be polite to the data source
    return out


def fetch_many(symbols: list[str], max_workers: int = 8) -> dict[str, pd.DataFrame]:
    """Fetch many symbols for a full-market scan.

    TradingView is the PRIMARY source here: the scanner gave us TradingView
    symbol names, which Yahoo mostly can't resolve (Yahoo needs numeric codes
    like 1155.KL). tvDatafeed's websocket is quick (~0.3-0.5s/symbol) but not
    thread-safe, so pass 1 is sequential. Yahoo concurrently gap-fills any
    misses that have a YAHOO_MAP entry."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: dict[str, pd.DataFrame] = {}

    # pass 1: TradingView, sequential (single websocket session)
    for i, s in enumerate(symbols, 1):
        df = fetch_tradingview(s)
        if df is not None and len(df) >= 60:
            out[s] = df
        if i % 50 == 0:
            log.info(f"TradingView: {i}/{len(symbols)} fetched, {len(out)} ok")
        time.sleep(0.1)

    # pass 2: Yahoo (concurrent) for anything TradingView missed
    missing = [s for s in symbols if s not in out]
    if missing:
        log.info(f"gap-filling {len(missing)} symbols via Yahoo")
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(fetch_yahoo, s): s for s in missing}
            for fut in as_completed(futures):
                s = futures[fut]
                try:
                    df = fut.result()
                    if df is not None and len(df) >= 60:
                        out[s] = df
                except Exception as e:
                    log.warning(f"{s}: {e}")
    log.info(f"total data ok: {len(out)}/{len(symbols)}")
    return out


def fetch_market() -> dict[str, pd.DataFrame]:
    """Fetch data for the screening universe.

    USE_FULL_MARKET=True  -> whole Bursa (optionally pre-filtered) via scanner
    USE_FULL_MARKET=False -> the manual WATCHLIST
    Falls back to WATCHLIST automatically if the scanner API fails."""
    if not config.USE_FULL_MARKET:
        return fetch_watchlist()

    try:
        from universe import get_universe, prefilter_candidates
        uni = get_universe()
        if config.UNIVERSE["use_prefilter"]:
            symbols = prefilter_candidates(uni)
        else:
            symbols = uni["symbol"].dropna().unique().tolist()
        if not symbols:
            raise RuntimeError("universe returned no symbols")
        log.info(f"full-market scan: downloading history for {len(symbols)} stocks")
        return fetch_many(symbols, max_workers=config.UNIVERSE["max_workers"])
    except Exception as e:
        log.error(f"full-market universe failed ({e}); falling back to WATCHLIST")
        return fetch_watchlist()


if __name__ == "__main__":
    d = fetch("MAYBANK")
    print(d.tail() if d is not None else "no data")
