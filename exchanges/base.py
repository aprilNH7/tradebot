"""Abstract base class for all exchange connectors."""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

# Terminal states — polling further will never produce a fill price.
_DEAD_ORDER_STATUSES = {
    "canceled", "cancelled", "rejected", "expired", "done_for_day", "suspended",
}


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
    # How much actually filled. Market orders fill whole, but a resting limit
    # order can fill in part and then be cancelled, leaving a position smaller
    # than the one requested. Booking the requested size in that case would make
    # the exit order too large — on a long that overshoots into an accidental
    # short. None means the venue did not report it.
    filled_quantity: Optional[float] = None

    def effective_filled_quantity(self) -> float:
        """Quantity we can actually prove is on the books.

        Falls back to the requested size only when the venue reports the order
        as fully filled. Anything unknown counts as zero, so an unreported
        partial can never be mistaken for a complete fill.
        """
        if self.filled_quantity is not None:
            return max(0.0, float(self.filled_quantity))
        if str(self.status).strip().lower() == "filled":
            return float(self.quantity)
        return 0.0


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

    def is_market_open(self) -> bool:
        """Whether the market is currently tradeable.

        Crypto and forex run effectively around the clock, so the default is
        True. Exchanges with session hours (equities) should override this.
        """
        return True

    def supports_fractional(self) -> bool:
        """Whether fractional quantities are allowed. Equities default to whole
        shares unless the connector says otherwise."""
        return self.market_type != MarketType.STOCK

    def wait_for_fill(self, order: Order, timeout: float = 5.0,
                      poll_interval: float = 0.25) -> Order:
        """Poll until the order reports an average fill price.

        A trade must be booked at the price it actually filled at, not the quote
        observed before submission. A market order crosses the spread, so
        booking the pre-trade quote credits that spread as profit on both legs
        of every round trip and makes a losing strategy look profitable.

        Returns the freshest Order state. `filled_price` may still be None if the
        order has not filled inside `timeout` — callers must handle that.
        """
        deadline = time.monotonic() + timeout
        latest = order
        while True:
            try:
                latest = self.get_order_status(order.id, order.symbol)
            except Exception:
                return latest
            status = str(latest.status).strip().lower()
            # A large market order fills in pieces, and filled_avg_price only
            # averages the pieces so far. Wait for the terminal "filled" state so
            # the booked price covers the whole order, not just the first slice.
            if status == "filled" and latest.filled_price:
                return latest
            if status in _DEAD_ORDER_STATUSES:
                return latest
            if time.monotonic() >= deadline:
                return latest
            time.sleep(poll_interval)

