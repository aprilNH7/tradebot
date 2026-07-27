"""Abstract base class for all exchange connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"


class MarketType(Enum):
    CRYPTO = "crypto"
    STOCK = "stock"
    FOREX = "forex"


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    timestamp: datetime


@dataclass
class OHLCV:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    filled_price: Optional[float] = None
    status: str = "pending"
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.side == OrderSide.BUY:
            return ((self.current_price - self.entry_price) / self.entry_price) * 100
        else:
            return ((self.entry_price - self.current_price) / self.entry_price) * 100


class BaseExchange(ABC):
    """Abstract exchange interface — all connectors implement this."""

    market_type: MarketType

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection and verify credentials."""
        pass

    @abstractmethod
    def get_ticker(self, symbol: str) -> Ticker:
        """Get current price data for a symbol."""
        pass

    @abstractmethod
    def get_ohlcv(self, symbol: str, timeframe: str = "1h",
                  limit: int = 100) -> list[OHLCV]:
        """Get historical OHLCV candles."""
        pass

    @abstractmethod
    def get_balance(self) -> dict:
        """Get account balance."""
        pass

    @abstractmethod
    def place_order(self, symbol: str, side: OrderSide,
                    order_type: OrderType, quantity: float,
                    price: Optional[float] = None) -> Order:
        """Place a trade order."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order."""
        pass

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Get all open positions."""
        pass

    @abstractmethod
    def get_order_status(self, order_id: str, symbol: str) -> Order:
        """Check status of an order."""
        pass
