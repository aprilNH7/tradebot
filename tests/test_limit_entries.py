"""Regression tests for passive limit entries.

Market entries crossed the spread on every trade. Across 4,892 round trips in
the 180-day backtest that cost more than the strategies' entire gross edge, so
entries now rest on the passive side of the book.

The dangerous part of that change is not the pricing, it is the fills. A market
order effectively always fills; a resting limit order often does not. The old
code booked `current_price` whenever no fill price came back, which was harmless
for market orders but would invent a position out of nothing for a limit order —
and the bot would then fire an exit order against a position the account never
held. These tests pin that down.
"""

import pytest

from exchanges.base import (
    BaseExchange, MarketType, Order, OrderSide, OrderType, Position, Ticker,
)


class FakeExchange(BaseExchange):
    """Configurable limit-order venue.

    `fill_qty` / `fill_price` describe what the resting order does inside the
    timeout. `fill_on_cancel` simulates the race where a fill lands between the
    final poll and the cancel request.
    """

    def __init__(self, fill_qty=None, fill_price=None, status="new",
                 fill_on_cancel=None, fractional=False):
        self.market_type = MarketType.STOCK
        self.fill_qty = fill_qty
        self.fill_price = fill_price
        self.status = status
        self.fill_on_cancel = fill_on_cancel
        self.fractional = fractional
        self.placed = []
        self.cancelled = []
        self._cancel_done = False

    def supports_fractional(self):
        return self.fractional

    def place_order(self, symbol, side, order_type, quantity, price=None):
        order = Order(id=f"o{len(self.placed)}", symbol=symbol, side=side,
                      order_type=order_type, quantity=quantity,
                      price=price, status="new")
        self.placed.append(order)
        return order

    def get_order_status(self, order_id, symbol):
        if self._cancel_done and self.fill_on_cancel is not None:
            qty, price = self.fill_on_cancel
            return Order(id=order_id, symbol=symbol, side=OrderSide.BUY,
                         order_type=OrderType.LIMIT, quantity=10.0,
                         filled_quantity=qty, filled_price=price,
                         status="filled")
        return Order(id=order_id, symbol=symbol, side=OrderSide.BUY,
                     order_type=OrderType.LIMIT, quantity=10.0,
                     filled_quantity=self.fill_qty,
                     filled_price=self.fill_price,
                     status=self.status)

    def cancel_order(self, order_id, symbol):
        self.cancelled.append(order_id)
        self._cancel_done = True
        return True

    def connect(self): return True
    def get_ticker(self, symbol): return Ticker(symbol, 99.98, 100.02, 100.0, 0, None)
    def get_ohlcv(self, symbol, timeframe="1h", limit=100): return []
    def get_balance(self): return {"equity": 100_000.0}
    def get_positions(self): return []


class Sig:
    """Stand-in for a strategy signal."""
    def __init__(self, signal, confidence=0.9, strategy="test"):
        self.signal = signal
        self.confidence = confidence
        self.strategy = strategy


@pytest.fixture
def engine():
    from core.engine import TradingEngine
    return TradingEngine()


@pytest.fixture(autouse=True)
def fast_limits(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "ENTRY_ORDER_TYPE", "limit")
    monkeypatch.setattr(settings, "LIMIT_PRICE_MODE", "passive")
    monkeypatch.setattr(settings, "LIMIT_ENTRY_TIMEOUT", 0.05)
    return settings


def quote(bid=99.98, ask=100.02, last=100.0):
    return Ticker("AAPL", bid, ask, last, 0, None)


def buy(engine, ex, size=1000.0, price=100.0, ticker=None):
    from strategies.base import Signal
    engine._execute_signal("AAPL", ex, "fake", Sig(Signal.BUY), size, price,
                           ticker if ticker is not None else quote())


# --------------------------------------------------------------------------
# The phantom position — the bug this change could have introduced
# --------------------------------------------------------------------------

def test_an_unfilled_entry_books_no_position(engine):
    """Nobody came to our bid. Nothing happened. The books must say so."""
    ex = FakeExchange(fill_qty=0.0, status="new")
    buy(engine, ex)

    assert not engine.risk_manager.has_position("AAPL")
    assert engine.portfolio.get_performance()["total_trades"] == 0


def test_an_unfilled_entry_is_cancelled(engine):
    """A resting day order left on the book can fill hours later, when the
    signal is long gone and the bot has no idea it owns anything."""
    ex = FakeExchange(fill_qty=0.0, status="new")
    buy(engine, ex)

    assert ex.cancelled == ["o0"]


def test_an_unfilled_entry_does_not_consume_the_daily_trade_budget(engine):
    ex = FakeExchange(fill_qty=0.0, status="new")
    buy(engine, ex)

    assert engine.risk_manager.metrics.daily_trades == 0
    assert engine.risk_manager.metrics.total_trades == 0


def test_a_filled_entry_is_booked_at_the_limit_price(engine):
    ex = FakeExchange(fill_qty=10.0, fill_price=99.98, status="filled")
    buy(engine, ex)

    pos = engine.risk_manager.get_position("AAPL")
    assert pos is not None
    assert pos["entry_price"] == 99.98
    assert pos["quantity"] == 10.0
    assert ex.cancelled == []


# --------------------------------------------------------------------------
# Partial fills
# --------------------------------------------------------------------------

def test_a_partial_fill_books_only_what_filled(engine):
    """Booking the requested 10 when only 4 filled would make the exit order
    2.5x too large — on a long that overshoots straight into a short."""
    ex = FakeExchange(fill_qty=4.0, fill_price=99.98, status="partially_filled")
    buy(engine, ex)

    pos = engine.risk_manager.get_position("AAPL")
    assert pos["quantity"] == 4.0


def test_a_partial_fill_cancels_the_remainder(engine):
    ex = FakeExchange(fill_qty=4.0, fill_price=99.98, status="partially_filled")
    buy(engine, ex)

    assert ex.cancelled == ["o0"]


def test_a_fill_landing_during_the_cancel_is_still_booked(engine):
    """The cancel request and the fill can cross on the wire. Re-reading the
    order after cancelling is what stops that becoming an untracked position."""
    ex = FakeExchange(fill_qty=0.0, status="new", fill_on_cancel=(10.0, 99.98))
    buy(engine, ex)

    pos = engine.risk_manager.get_position("AAPL")
    assert pos is not None
    assert pos["quantity"] == 10.0
    assert pos["entry_price"] == 99.98


def test_unknown_fill_quantity_counts_as_zero():
    """A venue that reports neither a filled quantity nor a terminal state has
    told us nothing. Guessing 'probably filled' is how phantom positions start.
    """
    o = Order(id="x", symbol="AAPL", side=OrderSide.BUY,
              order_type=OrderType.LIMIT, quantity=10.0, status="new")
    assert o.effective_filled_quantity() == 0.0


def test_a_fully_filled_order_without_a_quantity_field_uses_the_request():
    o = Order(id="x", symbol="AAPL", side=OrderSide.BUY,
              order_type=OrderType.LIMIT, quantity=10.0, status="filled")
    assert o.effective_filled_quantity() == 10.0


# --------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------

def test_a_buy_rests_at_the_bid(engine):
    ex = FakeExchange(fill_qty=10.0, fill_price=99.98, status="filled")
    buy(engine, ex, ticker=quote(bid=99.98, ask=100.02))

    order = ex.placed[0]
    assert order.order_type == OrderType.LIMIT
    assert order.price == 99.98          # not 100.02, and not the 100.00 last


def test_a_sell_rests_at_the_ask(engine):
    _, price = engine._entry_order_spec(OrderSide.SELL, quote(), 100.0)
    assert price == 100.02


def test_mid_mode_splits_the_spread(engine, fast_limits, monkeypatch):
    monkeypatch.setattr(fast_limits, "LIMIT_PRICE_MODE", "mid")
    _, price = engine._entry_order_spec(OrderSide.BUY, quote(99.98, 100.02), 100.0)
    assert price == 100.00


def test_tick_rounding_never_crosses_the_spread(engine, fast_limits, monkeypatch):
    """A mid of 100.005 must round down for a buy. Rounding up would pay the
    very half-spread this change exists to avoid, and sub-penny limits are
    rejected outright on shares over $1."""
    monkeypatch.setattr(fast_limits, "LIMIT_PRICE_MODE", "mid")

    _, bid_side = engine._entry_order_spec(OrderSide.BUY, quote(100.00, 100.01), 100.0)
    _, ask_side = engine._entry_order_spec(OrderSide.SELL, quote(100.00, 100.01), 100.0)

    assert bid_side == 100.00
    assert ask_side == 100.01
    assert round(bid_side * 100) == bid_side * 100    # whole cents


def test_a_crossed_quote_falls_back_to_the_last_trade(engine):
    """Crossed or empty quotes mean a stale feed. Posting a limit derived from
    it could rest miles away or cross badly."""
    _, price = engine._entry_order_spec(OrderSide.BUY, quote(101.0, 99.0), 100.0)
    assert price == 100.00

    _, price = engine._entry_order_spec(OrderSide.BUY, quote(0.0, 0.0), 100.0)
    assert price == 100.00


def test_market_mode_is_still_available(engine, fast_limits, monkeypatch):
    monkeypatch.setattr(fast_limits, "ENTRY_ORDER_TYPE", "market")
    order_type, price = engine._entry_order_spec(OrderSide.BUY, quote(), 100.0)

    assert order_type == OrderType.MARKET
    assert price is None


def test_market_mode_books_the_trade_without_a_cancel(engine, fast_limits, monkeypatch):
    monkeypatch.setattr(fast_limits, "ENTRY_ORDER_TYPE", "market")
    ex = FakeExchange(fill_qty=10.0, fill_price=100.02, status="filled")
    buy(engine, ex)

    assert ex.placed[0].order_type == OrderType.MARKET
    assert engine.risk_manager.get_position("AAPL")["entry_price"] == 100.02
    assert ex.cancelled == []


# --------------------------------------------------------------------------
# Exits stay market
# --------------------------------------------------------------------------

def test_exits_use_market_orders(engine):
    """A resting stop-loss is a stop-loss that might not happen, and the moment
    it fails is exactly the fast move it was supposed to protect against."""
    ex = FakeExchange(fill_qty=10.0, fill_price=97.0, status="filled")
    engine.risk_manager.register_trade("AAPL", "buy", 10.0, 100.0)
    engine.portfolio.record_trade(__import__(
        "core.portfolio", fromlist=["TradeRecord"]).TradeRecord(
            symbol="AAPL", side="buy", quantity=10.0, entry_price=100.0))

    engine._close_position("AAPL", ex, 97.0, "stop_loss")

    assert ex.placed[0].order_type == OrderType.MARKET
    assert not engine.risk_manager.has_position("AAPL")
