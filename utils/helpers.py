import time
from datetime import datetime
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def timestamp_ms() -> int:
    """Return the current Unix timestamp in milliseconds."""
    return int(time.time() * 1000)


def format_price(price: float, decimals: int = 8) -> str:
    """Format a numeric price as a string, trimming trailing zeros."""
    return f"{price:.{decimals}f}".rstrip("0").rstrip(".")


def format_pnl(pnl: float) -> str:
    """Format a PnL value as a percentage string with an explicit sign."""
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.2f}%"


def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    """Divide two floats, returning `default` when the divisor is zero."""
    return a / b if b != 0 else default


def retry(func: Callable[[], T], max_retries: int = 3, delay: float = 1.0) -> T:
    """Retry a function call with exponential backoff."""
    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(delay * (2 ** attempt))
    raise last_error
