"""Regression tests for PnL sign in the portfolio ledger.

`Portfolio.close_trade` had the same case-sensitive `side == "BUY"` check as the
risk manager. Since trades are recorded with `OrderSide.BUY.value` ("buy"), every
long was priced with the short formula, so the dashboard's reported PnL, win
rate and ROI all carried the wrong sign.
"""

import pytest

from core.portfolio import Portfolio, TradeRecord
from exchanges.base import OrderSide
from strategies.base import Signal

LONG_SIDES = ["buy", "BUY", "Buy", " buy ", OrderSide.BUY.value, Signal.BUY.value]
SHORT_SIDES = ["sell", "SELL", "Sell", OrderSide.SELL.value, Signal.SELL.value]


@pytest.fixture
def pf():
    p = Portfolio()
    p.set_initial_capital(100_000.0)
    return p


def record(pf, symbol, side, quantity, entry_price, strategy="test"):
    trade = TradeRecord(
        symbol=symbol, side=side, quantity=quantity,
        entry_price=entry_price, strategy=strategy, market="STOCK",
    )
    pf.record_trade(trade)
    return trade


# --------------------------------------------------------------------------
# PnL sign
# --------------------------------------------------------------------------

@pytest.mark.parametrize("side", LONG_SIDES)
def test_long_that_falls_is_a_loss(pf, side):
    """Real case from the logs: 27 PLTR exited below entry is -$4.08."""
    record(pf, "PLTR", side, 27, 63.15)
    closed = pf.close_trade("PLTR", 63.0)

    assert closed is not None
    assert closed.pnl == pytest.approx(-4.05, abs=0.01)
    assert pf.get_total_pnl() < 0


@pytest.mark.parametrize("side", LONG_SIDES)
def test_long_that_rises_is_a_gain(pf, side):
    record(pf, "X", side, 10, 100.0)
    closed = pf.close_trade("X", 105.0)

    assert closed.pnl == pytest.approx(50.0)
    assert pf.get_total_pnl() == pytest.approx(50.0)


@pytest.mark.parametrize("side", SHORT_SIDES)
def test_short_pnl_is_inverted(pf, side):
    record(pf, "X", side, 10, 100.0)
    closed = pf.close_trade("X", 95.0)

    assert closed.pnl == pytest.approx(50.0)


# --------------------------------------------------------------------------
# Ledger bookkeeping
# --------------------------------------------------------------------------

def test_open_and_closed_trades_are_partitioned(pf):
    record(pf, "A", OrderSide.BUY.value, 1, 10.0)
    record(pf, "B", OrderSide.BUY.value, 1, 20.0)
    pf.close_trade("A", 11.0)

    assert [t.symbol for t in pf.get_closed_trades()] == ["A"]
    assert [t.symbol for t in pf.get_open_trades()] == ["B"]


def test_close_targets_the_most_recent_open_lot(pf):
    """Two open lots on one symbol: the newest must close first."""
    record(pf, "X", OrderSide.BUY.value, 10, 100.0)
    record(pf, "X", OrderSide.BUY.value, 10, 200.0)

    closed = pf.close_trade("X", 210.0)
    assert closed.entry_price == 200.0
    assert closed.pnl == pytest.approx(100.0)

    still_open = pf.get_open_trades()
    assert len(still_open) == 1
    assert still_open[0].entry_price == 100.0


def test_closing_an_already_closed_symbol_returns_none(pf):
    record(pf, "X", OrderSide.BUY.value, 10, 100.0)
    pf.close_trade("X", 105.0)
    assert pf.close_trade("X", 110.0) is None


def test_closing_unknown_symbol_returns_none(pf):
    assert pf.close_trade("NOPE", 100.0) is None


# --------------------------------------------------------------------------
# Reported performance follows the corrected sign
# --------------------------------------------------------------------------

def test_a_losing_long_is_reported_as_a_loser(pf):
    record(pf, "X", OrderSide.BUY.value, 10, 100.0)
    pf.close_trade("X", 90.0)

    perf = pf.get_performance()
    assert perf["losers"] == 1
    assert perf["winners"] == 0
    assert perf["win_rate"] == "0.0%"
    assert perf["total_pnl"] == "$-100.00"
    assert perf["roi"] == "-0.10%"


def test_strategy_breakdown_attributes_the_loss(pf):
    record(pf, "X", OrderSide.BUY.value, 10, 100.0, strategy="grid")
    pf.close_trade("X", 90.0)

    breakdown = pf.get_strategy_breakdown()
    assert breakdown["grid"]["trades"] == 1
    assert breakdown["grid"]["wins"] == 0
    assert breakdown["grid"]["pnl"] == pytest.approx(-100.0)


def test_performance_is_safe_with_no_closed_trades(pf):
    perf = pf.get_performance()
    assert perf["total_trades"] == 0
    assert perf["win_rate"] == "N/A"
    assert perf["profit_factor"] == "N/A"
