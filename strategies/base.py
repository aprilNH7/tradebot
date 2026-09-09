"""Base strategy class — all strategies inherit from this."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from exchanges.base import OHLCV


__all__ = ["Signal", "TradeSignal", "BaseStrategy"]


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    signal: Signal
    symbol: str
    confidence: float  # 0.0 - 1.0
    strategy: str
    reason: str
    suggested_quantity: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class BaseStrategy(ABC):
    """Abstract strategy interface."""

    name: str = "base"

    @abstractmethod
    def analyze(self, symbol: str, candles: list[OHLCV],
                current_price: float) -> TradeSignal:
        """Analyze market data and return a trade signal."""
        pass

    @abstractmethod
    def get_params(self) -> dict:
        """Return current strategy parameters."""
        pass
