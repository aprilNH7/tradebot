"""Regression tests for the side-casing bug that inverted PnL and risk checks.

`OrderSide.BUY.value` is `"buy"` (lowercase) but `Signal.BUY.value` is `"BUY"`.
The risk manager stored whatever a caller handed it and compared against the
literal `"BUY"`, so a long opened via `OrderSide.BUY.value` was classified as a
short. That one mismatch broke three things at once:

  * PnL sign  — losses were booked as wins
  * stop-loss — fired on gains, never on real losses
  * the duplicate-position guard — never matched, so positions could stack

Every test is parametrised over each casing a caller might plausibly pass, so
the suite fails if any comparison regresses to a case-sensitive one.
"""

import pytest

from core.risk_manager import RiskManager
from exchanges.base import OrderSide
from strategies.base import Signal, TradeSignal

# Both enums plus hand-written casings callers have used.
LONG_SIDES = ["buy", "BUY", "Buy", " buy ", OrderSide.BUY.value, Signal.BUY.value]
SHORT_SIDES = ["sell", "SELL", "Sell", OrderSide.SELL.value, Signal.SELL.value]


@pytest.fixture
def rm():
    """Risk manager with limits pinned so tests never depend on .env."""
    r = RiskManager()
    r.stop_loss_pct = 0.02
    r.take_profit_pct = 0.05
    r.max_daily_trades = 100
    r.max_open_positions = 10
    r.min_confidence = 0.5
    r.max_position_size = 0.05
    r.update_balance(100_000.0)
    return r


def signal(kind: Signal, symbol: str = "X", confidence: float = 0.7) -> TradeSignal:
    return TradeSignal(
        signal=kind, symbol=symbol, confidence=confidence,
        strategy="test", reason="test",
    )


# --------------------------------------------------------------------------
# Side normalisation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("side", LONG_SIDES)
def test_every_long_casing_normalises_to_buy(rm, side):
    rm.register_trade("X", side, 1, 100.0)
    assert rm.get_position("X")["side"] == "buy"


@pytest.mark.parametrize("side", SHORT_SIDES)
def test_every_short_casing_normalises_to_sell(rm, side):
    rm.register_trade("X", side, 1, 100.0)
    assert rm.get_position("X")["side"] == "sell"


@pytest.mark.parametrize("side", LONG_SIDES)
def test_long_is_classified_long(rm, side):
    rm.register_trade("X", side, 1, 100.0)
    assert rm._is_long(rm.get_position("X")) is True


@pytest.mark.parametrize("side", SHORT_SIDES)
def test_short_is_not_classified_long(rm, side):
    rm.register_trade("X", side, 1, 100.0)
    assert rm._is_long(rm.get_position("X")) is False


# --------------------------------------------------------------------------
# PnL sign — the bug that reported losses as gains
# --------------------------------------------------------------------------

@pytest.mark.parametrize("side", LONG_SIDES)
def test_long_that_falls_books_a_loss(rm, side):
    """Real case from the logs: 8 MSFT @ 390.895 exited at 390.575.

    True PnL is -$2.56. The old code reported +$2.56 and counted it a win.
    """
    rm.register_trade("MSFT", side, 8, 390.895)
    rm.close_position("MSFT", 390.575)

    assert rm.metrics.total_pnl == pytest.approx(-2.56, abs=0.01)
    assert rm.metrics.daily_pnl == pytest.approx(-2.56, abs=0.01)
    assert rm.metrics.losing_trades == 1
    assert rm.metrics.winning_trades == 0


@pytest.mark.parametrize("side", LONG_SIDES)
def test_long_that_rises_books_a_gain(rm, side):
    rm.register_trade("X", side, 10, 100.0)
    rm.close_position("X", 105.0)

    assert rm.metrics.total_pnl == pytest.approx(50.0)
    assert rm.metrics.winning_trades == 1
    assert rm.metrics.losing_trades == 0


@pytest.mark.parametrize("side", SHORT_SIDES)
def test_short_pnl_is_inverted(rm, side):
    rm.register_trade("X", side, 10, 100.0)
    rm.close_position("X", 95.0)

    assert rm.metrics.total_pnl == pytest.approx(50.0)
    assert rm.metrics.winning_trades == 1


def test_closing_unknown_symbol_is_a_noop(rm):
    rm.close_position("NOPE", 100.0)
    assert rm.metrics.total_pnl == 0.0
    assert rm.metrics.winning_trades == 0
    assert rm.metrics.losing_trades == 0


def test_close_removes_the_position(rm):
    rm.register_trade("X", OrderSide.BUY.value, 10, 100.0)
    assert rm.has_position("X") is True
    rm.close_position("X", 101.0)
    assert rm.has_position("X") is False
    assert rm.get_position("X") is None


# --------------------------------------------------------------------------
# Stop-loss / take-profit direction
# --------------------------------------------------------------------------

@pytest.mark.parametrize("side", LONG_SIDES)
def test_long_adverse_move_hits_stop_loss_only(rm, side):
    """A long dropping 3% must stop out and must not look like a profit."""
    rm.register_trade("X", side, 10, 100.0)
    assert rm.check_stop_loss("X", 97.0) is True
    assert rm.check_take_profit("X", 97.0) is False


@pytest.mark.parametrize("side", LONG_SIDES)
def test_long_favourable_move_hits_take_profit_only(rm, side):
    """A long up 6% must take profit — the old code stopped it out here."""
    rm.register_trade("X", side, 10, 100.0)
    assert rm.check_take_profit("X", 106.0) is True
    assert rm.check_stop_loss("X", 106.0) is False


@pytest.mark.parametrize("side", SHORT_SIDES)
def test_short_directions_are_mirrored(rm, side):
    rm.register_trade("X", side, 10, 100.0)
    assert rm.check_stop_loss("X", 103.0) is True
    assert rm.check_take_profit("X", 103.0) is False
    assert rm.check_take_profit("X", 94.0) is True
    assert rm.check_stop_loss("X", 94.0) is False


@pytest.mark.parametrize("side", LONG_SIDES)
def test_small_move_triggers_nothing(rm, side):
    rm.register_trade("X", side, 10, 100.0)
    assert rm.check_stop_loss("X", 99.5) is False
    assert rm.check_take_profit("X", 100.5) is False


def test_checks_on_unknown_symbol_are_false(rm):
    assert rm.check_stop_loss("NOPE", 1.0) is False
    assert rm.check_take_profit("NOPE", 1.0) is False


# --------------------------------------------------------------------------
# Duplicate-position guard
# --------------------------------------------------------------------------

@pytest.mark.parametrize("side", LONG_SIDES)
def test_second_buy_on_an_open_long_is_rejected(rm, side):
    """The guard compares a stored side against Signal.BUY.value ("BUY").

    Before normalisation this never matched, so the bot could stack the same
    symbol on every scan cycle.
    """
    rm.register_trade("X", side, 10, 100.0)
    approved, size = rm.evaluate_signal(signal(Signal.BUY), 100_000.0)
    assert approved is False
    assert size == 0


@pytest.mark.parametrize("side", SHORT_SIDES)
def test_second_sell_on_an_open_short_is_rejected(rm, side):
    rm.register_trade("X", side, 10, 100.0)
    approved, _ = rm.evaluate_signal(signal(Signal.SELL), 100_000.0)
    assert approved is False


def test_opposite_direction_is_still_allowed(rm):
    """A SELL against an open long is the exit path — it must not be blocked."""
    rm.register_trade("X", OrderSide.BUY.value, 10, 100.0)
    approved, size = rm.evaluate_signal(signal(Signal.SELL), 100_000.0)
    assert approved is True
    assert size > 0


def test_signal_on_untouched_symbol_is_approved(rm):
    approved, size = rm.evaluate_signal(signal(Signal.BUY, symbol="FRESH"), 100_000.0)
    assert approved is True
    # 5% of 100k scaled by 0.7 confidence
    assert size == pytest.approx(3_500.0)


# --------------------------------------------------------------------------
# Trade accounting
# --------------------------------------------------------------------------

def test_register_trade_counts_toward_limits_by_default(rm):
    rm.register_trade("X", OrderSide.BUY.value, 1, 100.0)
    assert rm.metrics.total_trades == 1
    assert rm.metrics.daily_trades == 1


def test_adopted_positions_do_not_consume_the_daily_budget(rm):
    """Positions adopted from the exchange on restart were not opened by this
    session, so they must not burn the daily trade cap."""
    rm.register_trade("X", OrderSide.BUY.value, 1, 100.0, count_toward_limits=False)
    assert rm.has_position("X") is True
    assert rm.metrics.total_trades == 0
    assert rm.metrics.daily_trades == 0
