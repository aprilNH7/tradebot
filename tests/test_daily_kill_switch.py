"""Regression tests for the daily loss kill-switch.

Before this existed the only hard floor was RISK_MAX_DRAWDOWN at 10%, measured
against the all-time peak. On a $100k account that lets a single bad session
burn $10,000 before anything reacts, and because it is peak-relative it gets
*looser* after a losing week. There was no per-day limit at all.

The switch is deliberately measured against the day's opening equity rather
than realised PnL, so a position that is quietly bleeding counts against the
cap immediately instead of only when it is finally closed.
"""

import pytest
from datetime import datetime, timedelta

from core.risk_manager import RiskManager
from strategies.base import Signal, TradeSignal


def make_rm(cap=200.0, opening_equity=100_000.0):
    rm = RiskManager()
    rm.max_daily_loss = cap
    if opening_equity:
        rm.update_balance(opening_equity)
    return rm


def buy_signal(symbol="AAPL", confidence=0.9):
    return TradeSignal(
        symbol=symbol, signal=Signal.BUY, confidence=confidence,
        reason="test", strategy="test",
    )


# --------------------------------------------------------------------------
# Tripping the switch
# --------------------------------------------------------------------------

def test_unrealised_loss_trips_the_cap():
    """An open position bleeding out counts — no need to realise the loss."""
    rm = make_rm(cap=200.0)
    rm.update_balance(99_799.0)          # down $201, nothing closed

    assert rm.is_halted
    can, why = rm.can_trade()
    assert not can
    assert "Daily loss cap" in why


def test_loss_below_the_cap_keeps_trading():
    rm = make_rm(cap=200.0)
    rm.update_balance(99_850.0)          # down $150

    assert not rm.is_halted
    assert rm.can_trade()[0]


def test_cap_blocks_position_sizing_not_just_can_trade():
    """evaluate_signal is the call the engine actually makes."""
    rm = make_rm(cap=200.0)
    rm.update_balance(99_700.0)

    approved, size = rm.evaluate_signal(buy_signal(), 99_700.0)
    assert not approved
    assert size == 0


def test_realised_loss_trips_the_cap_without_an_equity_feed():
    """The backtest harness never calls update_balance — the cap must still bind."""
    rm = RiskManager()
    rm.max_daily_loss = 200.0

    rm.register_trade("AAPL", "buy", 100, 100.0)
    rm.close_position("AAPL", 97.0)      # -$300 realised

    assert rm.metrics.daily_loss == pytest.approx(300.0)
    assert rm.is_halted
    assert not rm.can_trade()[0]


def test_a_disabled_cap_never_halts():
    rm = make_rm(cap=0.0)
    rm.update_balance(97_000.0)          # down $3k, well past any sane cap

    assert not rm.is_halted
    assert rm.can_trade()[0]             # only the 10% drawdown gate remains


# --------------------------------------------------------------------------
# The latch — the part that is easy to get wrong
# --------------------------------------------------------------------------

def test_recovery_does_not_release_the_halt():
    """Once the day is lost it stays lost.

    Without a latch, equity ticking back above the threshold would silently
    re-arm the bot on the same day that just proved the strategy was wrong —
    and the cap would fire again, and again, each time letting through more
    trades. The switch must be one-way until the date changes.
    """
    rm = make_rm(cap=200.0)
    rm.update_balance(99_700.0)
    assert rm.is_halted

    rm.update_balance(100_500.0)         # fully recovered and then some
    assert rm.is_halted
    assert not rm.can_trade()[0]


def test_halt_survives_repeated_checks():
    rm = make_rm(cap=200.0)
    rm.update_balance(99_700.0)
    for _ in range(5):
        assert not rm.can_trade()[0]


# --------------------------------------------------------------------------
# Releasing on a new day
# --------------------------------------------------------------------------

def test_new_day_releases_the_halt():
    rm = make_rm(cap=200.0)
    rm.update_balance(99_700.0)
    assert rm.is_halted

    rm.metrics.last_reset = datetime.now() - timedelta(days=1)
    assert rm.maybe_reset_daily()

    assert not rm.is_halted
    assert rm.can_trade()[0]


def test_yesterdays_loss_does_not_count_against_today():
    """The opening-equity anchor has to move with the day.

    If day_start_balance stayed at yesterday's open, the account would show as
    already down $300 the moment the new session began and the switch would
    trip on the first equity read — permanently bricking the bot.
    """
    rm = make_rm(cap=200.0)
    rm.update_balance(99_700.0)          # ended yesterday down $300

    rm.metrics.last_reset = datetime.now() - timedelta(days=1)
    rm.maybe_reset_daily()

    assert rm.metrics.day_start_balance == pytest.approx(99_700.0)
    assert rm.metrics.daily_loss == pytest.approx(0.0)
    assert rm.can_trade()[0]


# --------------------------------------------------------------------------
# What must keep working while halted
# --------------------------------------------------------------------------

def test_stops_still_fire_while_halted():
    """A kill-switch that also disabled exits would strand the losing position
    it was triggered by. Only new entries are refused."""
    rm = make_rm(cap=200.0)
    rm.register_trade("AAPL", "buy", 100, 100.0)
    rm.update_balance(99_700.0)
    assert rm.is_halted

    assert rm.check_stop_loss("AAPL", 97.0)      # -3% vs a 2% stop
    assert rm.check_take_profit("AAPL", 105.0)   # +5% vs a 4% target


def test_closing_out_while_halted_still_books_pnl():
    rm = make_rm(cap=200.0)
    rm.register_trade("AAPL", "buy", 100, 100.0)
    rm.update_balance(99_700.0)

    rm.close_position("AAPL", 98.0)
    assert rm.metrics.total_pnl == pytest.approx(-200.0)
    assert not rm.has_position("AAPL")


def test_status_reports_the_halt():
    rm = make_rm(cap=200.0)
    rm.update_balance(99_700.0)

    status = rm.get_status()
    assert status["halted"] is True
    assert status["daily_loss"] == "$300.00"
    assert status["daily_loss_cap"] == "$200.00"


# --------------------------------------------------------------------------
# daily_loss arithmetic
# --------------------------------------------------------------------------

def test_daily_loss_is_never_negative_on_a_winning_day():
    rm = make_rm(cap=200.0)
    rm.update_balance(101_000.0)
    assert rm.metrics.daily_loss == 0.0
    assert not rm.is_halted


def test_equity_delta_wins_over_realised_pnl():
    """Realised +$500 while total equity is down $400 means open positions are
    down $900. The cap must see the $400, not the flattering +$500."""
    rm = make_rm(cap=200.0)
    rm.metrics.daily_pnl = 500.0
    rm.update_balance(99_600.0)

    assert rm.metrics.daily_loss == pytest.approx(400.0)
    assert rm.is_halted
