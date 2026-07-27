"""Regression tests: trades must be booked at the actual fill price.

`place_order` returns as soon as the exchange accepts the order, before any fill
price is known. The engine used to book `current_price` — the quote read *before*
submission — for both the entry and the exit.

A market order crosses the spread, so the real entry is worse than the quote and
the real exit is also worse. Booking the quote on both legs credits the spread as
profit on every round trip. On a live paper account this reported +$136.90 while
the account had actually made +$39.66: a 3.4x overstatement that would make a
losing strategy look like a winner.
"""

import pytest

from exchanges.base import (
    BaseExchange, MarketType, Order, OrderSide, OrderType, Position, Ticker,
)


class FakeExchange(BaseExchange):
    """Minimal exchange that fills at a price different from the quote."""

    def __init__(self, fill_price=None, status="filled", fills_after=0):
        self.market_type = MarketType.STOCK
        self.fill_price = fill_price
        self.status = status
        self.fills_after = fills_after   # polls before a fill price appears
        self.polls = 0
        self.orders = []

    # --- the parts under test -------------------------------------------
    def place_order(self, symbol, side, order_type, quantity, price=None):
        order = Order(id=f"o{len(self.orders)}", symbol=symbol, side=side,
                      order_type=order_type, quantity=quantity, status="new")
        self.orders.append(order)
        return order

    def get_order_status(self, order_id, symbol):
        self.polls += 1
        ready = self.polls > self.fills_after
        return Order(
            id=order_id, symbol=symbol, side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=1.0,
            filled_price=self.fill_price if ready else None,
            status=self.status,
        )

    # --- unused abstract members ----------------------------------------
    def connect(self): return True
    def get_ticker(self, symbol): return Ticker(symbol, 0, 0, 0, 0, None)
    def get_ohlcv(self, symbol, timeframe="1h", limit=100): return []
    def get_balance(self): return {"equity": 100_000.0}
    def cancel_order(self, order_id, symbol): return True
    def get_positions(self): return []


class ExplodingExchange(FakeExchange):
    def get_order_status(self, order_id, symbol):
        raise RuntimeError("network down")


class PartialFillExchange(FakeExchange):
    """Reports a partial average first, then the true full-order average.

    Alpaca fills a large market order in slices and `filled_avg_price` only
    averages the slices completed so far, so the first non-null price can be
    several cents away from the final cost basis.
    """

    def __init__(self):
        super().__init__()
        self.sequence = [
            ("partially_filled", 211.60),
            ("partially_filled", 211.58),
            ("filled", 211.5475),
        ]

    def get_order_status(self, order_id, symbol):
        status, price = self.sequence[min(self.polls, len(self.sequence) - 1)]
        self.polls += 1
        return Order(
            id=order_id, symbol=symbol, side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=16.0,
            filled_price=price, status=status,
        )


@pytest.fixture
def engine():
    from core.engine import TradingEngine
    return TradingEngine()


# --------------------------------------------------------------------------
# wait_for_fill
# --------------------------------------------------------------------------

def test_wait_for_fill_returns_the_fill_price():
    ex = FakeExchange(fill_price=101.25)
    order = ex.place_order("X", OrderSide.BUY, OrderType.MARKET, 1)
    assert ex.wait_for_fill(order).filled_price == 101.25


def test_wait_for_fill_polls_until_the_fill_appears():
    ex = FakeExchange(fill_price=99.5, fills_after=2)
    order = ex.place_order("X", OrderSide.BUY, OrderType.MARKET, 1)
    filled = ex.wait_for_fill(order, timeout=2.0, poll_interval=0.01)
    assert filled.filled_price == 99.5
    assert ex.polls == 3


def test_wait_for_fill_gives_up_on_a_rejected_order():
    """A terminal status will never produce a fill — stop immediately."""
    ex = FakeExchange(fill_price=None, status="rejected")
    order = ex.place_order("X", OrderSide.BUY, OrderType.MARKET, 1)
    filled = ex.wait_for_fill(order, timeout=5.0, poll_interval=0.01)
    assert filled.filled_price is None
    assert ex.polls == 1  # did not burn the full timeout


def test_wait_for_fill_respects_timeout_when_never_filled():
    ex = FakeExchange(fill_price=None, status="new")
    order = ex.place_order("X", OrderSide.BUY, OrderType.MARKET, 1)
    filled = ex.wait_for_fill(order, timeout=0.05, poll_interval=0.01)
    assert filled.filled_price is None


def test_partial_fill_average_is_not_accepted_early():
    """Real BA case: the first partial average was 211.60, the true cost basis
    211.5475. Accepting the partial mis-booked the trade by $0.36."""
    ex = PartialFillExchange()
    order = ex.place_order("BA", OrderSide.BUY, OrderType.MARKET, 16)
    filled = ex.wait_for_fill(order, timeout=2.0, poll_interval=0.01)
    assert filled.filled_price == 211.5475
    assert filled.status == "filled"


def test_partial_fill_is_still_returned_if_it_never_completes():
    """Better to book the partial average than to fall back to the quote."""
    ex = FakeExchange(fill_price=211.60, status="partially_filled")
    order = ex.place_order("BA", OrderSide.BUY, OrderType.MARKET, 16)
    filled = ex.wait_for_fill(order, timeout=0.05, poll_interval=0.01)
    assert filled.filled_price == 211.60


# --------------------------------------------------------------------------
# _fill_price — what actually gets booked
# --------------------------------------------------------------------------

def test_fill_price_prefers_the_actual_fill_over_the_quote(engine):
    """The whole point: 100.00 was quoted, 100.07 was paid. Book 100.07."""
    ex = FakeExchange(fill_price=100.07)
    order = ex.place_order("X", OrderSide.BUY, OrderType.MARKET, 1)
    assert engine._fill_price(ex, order, 100.00, "X") == 100.07


def test_fill_price_falls_back_to_quote_when_unfilled(engine):
    ex = FakeExchange(fill_price=None, status="new")
    order = ex.place_order("X", OrderSide.BUY, OrderType.MARKET, 1)
    assert engine._fill_price(ex, order, 100.00, "X") == 100.00


def test_fill_price_falls_back_to_quote_when_lookup_raises(engine):
    """A broken status endpoint must not take the trading loop down."""
    ex = ExplodingExchange()
    order = ex.place_order("X", OrderSide.BUY, OrderType.MARKET, 1)
    assert engine._fill_price(ex, order, 100.00, "X") == 100.00


# --------------------------------------------------------------------------
# The spread is no longer booked as profit
# --------------------------------------------------------------------------

def test_round_trip_at_the_same_quote_is_a_loss_not_a_wash(engine):
    """Buy and sell at an unchanged quote of 100.00, paying the spread both ways.

    Real cost: bought at 100.02, sold at 99.98 => -$0.04/share.
    Booking the 100.00 quote on both legs would report exactly $0.00 and hide
    the bleed that eventually drains the account.
    """
    from core.portfolio import Portfolio, TradeRecord

    buy_ex = FakeExchange(fill_price=100.02)
    sell_ex = FakeExchange(fill_price=99.98)
    buy_order = buy_ex.place_order("X", OrderSide.BUY, OrderType.MARKET, 100)
    sell_order = sell_ex.place_order("X", OrderSide.SELL, OrderType.MARKET, 100)

    entry = engine._fill_price(buy_ex, buy_order, 100.00, "X")
    exit_ = engine._fill_price(sell_ex, sell_order, 100.00, "X")

    pf = Portfolio()
    pf.record_trade(TradeRecord(symbol="X", side=OrderSide.BUY.value,
                                quantity=100, entry_price=entry))
    closed = pf.close_trade("X", exit_)

    assert closed.pnl == pytest.approx(-4.0, abs=0.01)
    assert pf.get_performance()["losers"] == 1
