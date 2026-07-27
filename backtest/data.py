"""Historical bar fetching with on-disk caching for backtests.

Sweeps re-run the same bars dozens of times, so bars are cached as JSON and
reused. This keeps parameter sweeps fast and lets them run without a network
connection or burning API rate limit.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from exchanges.base import OHLCV
from utils.logger import setup_logger

log = setup_logger("backtest.data")

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "bars"

_TF_MAP = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "1d": "1Day"}


def _cache_path(symbol: str, timeframe: str, days: int) -> Path:
    return CACHE_DIR / f"{symbol}_{timeframe}_{days}d.json"


def _to_dicts(candles: list[OHLCV]) -> list[dict]:
    return [
        {
            "timestamp": c.timestamp.isoformat(),
            "open": c.open, "high": c.high, "low": c.low,
            "close": c.close, "volume": c.volume,
        }
        for c in candles
    ]


def _from_dicts(rows: list[dict]) -> list[OHLCV]:
    return [
        OHLCV(
            timestamp=datetime.fromisoformat(r["timestamp"]),
            open=r["open"], high=r["high"], low=r["low"],
            close=r["close"], volume=r["volume"],
        )
        for r in rows
    ]


def load_bars(symbol: str, timeframe: str = "1h", days: int = 180,
              exchange=None, refresh: bool = False) -> list[OHLCV]:
    """Return historical bars, fetching and caching on first use."""
    path = _cache_path(symbol, timeframe, days)
    if path.exists() and not refresh:
        try:
            return _from_dicts(json.loads(path.read_text()))
        except Exception as e:
            log.warning(f"{symbol}: cache unreadable ({e}), refetching")

    if exchange is None:
        raise RuntimeError(
            f"No cached bars for {symbol} ({timeframe}, {days}d) and no exchange "
            f"given to fetch them."
        )

    tf = _TF_MAP.get(timeframe, "1Hour")
    start = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    df = exchange.client.get_bars(symbol, tf, start=start.isoformat()).df
    if df.empty:
        log.warning(f"{symbol}: no bars since {start}")
        return []

    candles = [
        OHLCV(
            timestamp=idx.to_pydatetime(),
            open=row["open"], high=row["high"], low=row["low"],
            close=row["close"], volume=row["volume"],
        )
        for idx, row in df.iterrows()
    ]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_dicts(candles)))
    log.info(f"{symbol}: cached {len(candles)} {timeframe} bars ({days}d)")
    return candles


def load_universe(symbols: list[str], timeframe: str = "1h", days: int = 180,
                  exchange=None, refresh: bool = False) -> dict[str, list[OHLCV]]:
    out = {}
    for s in symbols:
        try:
            bars = load_bars(s, timeframe, days, exchange, refresh)
            if bars:
                out[s] = bars
        except Exception as e:
            log.warning(f"{s}: skipped ({e})")
    return out
