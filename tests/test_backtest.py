"""Tests for the backtest harness.

A backtester that flatters the strategy is worse than no backtester at all: it
converts a losing system into a confident bet. These tests pin down the
properties that decide whether any number the harness prints can be trusted —
no lookahead, costs actually charged, risk limits actually enforced, and equity
accounting that cannot feed back into position sizing.
"""

import math
from datetime import datetime, timedelta

import pytest

from backtest import harness
from backtest.harness import Backtester
from exchanges.base import OHLCV
from strategies.base import BaseStrategy, Signal, TradeSignal
from strategies.grid_strategy import GridStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.sma_crossover import SMACrossoverStrategy

START = datetime(2026, 1, 5, 9, 30)


def bars(closes, highs=None, lows=None, start=START, step_minutes=5, symbol_offset=0):
    """Build a bar series. Highs/lows default to the close (a flat bar)."""
    out = []
    for i, c in enumerate(closes):
        hi = highs[i] if highs else c
        lo = lows[i] if lows else c
        out.append(OHLCV(
            timestamp=start + timedelta(minutes=step_minutes * i + symbol_offset),
            open=c, high=hi, low=lo, close=c, volume=1000.0,
        ))
    return out


class Scripted(BaseStrategy):
    """Emits a preset signal on a given bar index, HOLD otherwise."""

    name = "scripted"

    def __init__(self, script: dict[int, Signal], confidence=0.9):
        self.script = script
        self.confidence = confidence
        self.windows: list[list[OHLCV]] = []

    def analyze(self, symbol, candles, current_price):
        self.windows.append(candles)
        sig = self.script.get(len(candles) - 1, Signal.HOLD)
        return TradeSignal(signal=sig, symbol=symbol, confidence=self.confidence,
                           strategy=self.name, reason="scripted")

    def get_params(self):
        return {"script": self.script}


class AlwaysBuy(BaseStrategy):
    name = "always_buy"

    def __init__(self, confidence=0.9):
        self.confidence = confidence

    def analyze(self, symbol, candles, current_price):
        return TradeSignal(signal=Signal.BUY, symbol=symbol,
                           confidence=self.confidence, strategy=self.name,
                           reason="always")

    def get_params(self):
        return {}


# --------------------------------------------------------------------------
# No lookahead — the property that makes or breaks a backtest
# --------------------------------------------------------------------------
def test_strategy_never_sees_future_bars():
    closes = [100.0 + i for i in range(40)]
    series = bars(closes)
    strat = Scripted({})
    Backtester([strat], {"X": series}, warmup=25).run()

    assert strat.windows, "strategy was never called"
    for window in strat.windows:
        # The window must be a prefix of the full series, never reordered and
        # never containing a bar the harness has not yet reached.
        assert window == series[: len(window)]


def test_last_bar_in_window_is_the_current_bar():
    closes = [100.0 + i for i in range(35)]
    series = bars(closes)
    strat = Scripted({})
    Backtester([strat], {"X": series}, warmup=25).run()

    seen_last = [w[-1] for w in strat.windows]
    assert seen_last == series[25:]


def test_warmup_bars_are_not_traded():
    closes = [100.0] * 40
    strat = Scripted({})
    Backtester([strat], {"X": bars(closes)}, warmup=30).run()
    # First call sees 31 bars (indices 0..30).
    assert len(strat.windows[0]) == 31


# --------------------------------------------------------------------------
# Costs are real
# --------------------------------------------------------------------------
def test_round_trip_at_a_flat_price_loses_the_spread():
    """Buy and sell at the same quoted price must lose money, not break even."""
    closes = [100.0] * 40
    strat = Scripted({26: Signal.BUY, 30: Signal.SELL})
    r = Backtester([strat], {"X": bars(closes)}, spread_bps=10.0,
                   initial_capital=100_000.0).run()

    assert len(r.trips) == 1
    trip = r.trips[0]
    assert trip.pnl < 0, "a flat round trip must still pay the spread"
    # 10bps total on a ~100k notional round trip.
    expected = -trip.quantity * 100.0 * (10.0 / 10_000.0)
    assert trip.pnl == pytest.approx(expected, rel=1e-6)


def test_zero_spread_flat_round_trip_is_breakeven():
    closes = [100.0] * 40
    strat = Scripted({26: Signal.BUY, 30: Signal.SELL})
    r = Backtester([strat], {"X": bars(closes)}, spread_bps=0.0).run()
    assert r.trips[0].pnl == pytest.approx(0.0)


def test_buy_fill_is_worse_than_quote_and_sell_fill_is_better_for_the_market():
    bt = Backtester([], {}, spread_bps=20.0)
    assert bt._buy_fill(100.0) == pytest.approx(100.10)
    assert bt._sell_fill(100.0) == pytest.approx(99.90)


# --------------------------------------------------------------------------
# Stop-loss / take-profit resolution
# --------------------------------------------------------------------------
def test_stop_loss_wins_when_one_bar_spans_both_levels():
    """The conservative assumption must hold, or drawdowns are understated."""
    closes = [100.0] * 40
    # Bar 28 swings from -10% to +10%: it touches both stop and target.
    highs = list(closes)
    lows = list(closes)
    highs[28] = 110.0
    lows[28] = 90.0

    strat = Scripted({26: Signal.BUY})
    r = Backtester([strat], {"X": bars(closes, highs, lows)}, spread_bps=0.0,
                   risk_overrides={"stop_loss_pct": 0.02,
                                   "take_profit_pct": 0.05}).run()

    assert len(r.trips) == 1
    assert r.trips[0].exit_reason == "stop_loss"
    assert r.trips[0].pnl < 0


def test_take_profit_fires_when_only_the_target_is_touched():
    closes = [100.0] * 40
    highs = list(closes)
    highs[28] = 106.0
    strat = Scripted({26: Signal.BUY})
    r = Backtester([strat], {"X": bars(closes, highs, None)}, spread_bps=0.0,
                   risk_overrides={"stop_loss_pct": 0.02,
                                   "take_profit_pct": 0.05}).run()

    assert [t.exit_reason for t in r.trips] == ["take_profit"]
    assert r.trips[0].pnl > 0
    assert r.trips[0].exit_price == pytest.approx(105.0)


def test_stop_exit_price_is_the_stop_level_not_the_bar_close():
    """Exiting at the close would understate the loss on a gap-down bar."""
    closes = [100.0] * 40
    lows = list(closes)
    lows[28] = 80.0
    strat = Scripted({26: Signal.BUY})
    r = Backtester([strat], {"X": bars(closes, None, lows)}, spread_bps=0.0,
                   risk_overrides={"stop_loss_pct": 0.02,
                                   "take_profit_pct": 0.50}).run()
    assert r.trips[0].exit_price == pytest.approx(98.0)


def test_no_exit_when_bar_touches_neither_level():
    closes = [100.0] * 40
    strat = Scripted({26: Signal.BUY})
    r = Backtester([strat], {"X": bars(closes)}, spread_bps=0.0,
                   risk_overrides={"stop_loss_pct": 0.02,
                                   "take_profit_pct": 0.05}).run()
    assert [t.exit_reason for t in r.trips] == ["end_of_backtest"]


# --------------------------------------------------------------------------
# Equity accounting — the bug that printed trillions
# --------------------------------------------------------------------------
def test_short_positions_do_not_inflate_equity():
    """Regression: crediting short proceeds twice compounds size explosively.

    A short's sale proceeds are already in cash, so marking the position must
    subtract the buyback liability. Getting this wrong made equity grow with
    every short, which grew the next position, which grew equity again.
    """
    closes = [100.0] * 200
    strat = Scripted({26: Signal.SELL})
    r = Backtester([strat], {"X": bars(closes)}, spread_bps=0.0,
                   initial_capital=100_000.0,
                   risk_overrides={"stop_loss_pct": 0.99,
                                   "take_profit_pct": 0.99}).run()

    peak = max(eq for _, eq in r.equity_curve)
    assert peak < 100_000.0 * 1.05, f"equity inflated to {peak:,.0f} on a flat price"


def test_equity_curve_stays_finite_across_many_shorts():
    closes = [100.0 + (i % 7) for i in range(400)]
    strat = AlwaysBuy()

    class AlwaysShort(BaseStrategy):
        name = "always_short"

        def analyze(self, symbol, candles, current_price):
            return TradeSignal(signal=Signal.SELL, symbol=symbol, confidence=0.9,
                               strategy=self.name, reason="short")

        def get_params(self):
            return {}

    r = Backtester([AlwaysShort()], {"X": bars(closes)},
                   initial_capital=100_000.0).run()
    for _, eq in r.equity_curve:
        assert abs(eq) < 10_000_000.0, "equity ran away"


def test_long_round_trip_pnl_matches_price_difference():
    closes = [100.0] * 27 + [110.0] * 13
    strat = Scripted({26: Signal.BUY, 30: Signal.SELL})
    r = Backtester([strat], {"X": bars(closes)}, spread_bps=0.0,
                   risk_overrides={"stop_loss_pct": 0.99,
                                   "take_profit_pct": 0.99}).run()
    trip = r.trips[0]
    assert trip.pnl == pytest.approx((trip.exit_price - trip.entry_price)
                                     * trip.quantity)
    assert trip.pnl > 0


def test_opposing_signal_closes_the_position_instead_of_overwriting_it():
    """Regression: a BUY against an open short used to orphan the short.

    The old code fell through to the entry block and replaced open_pos[sym].
    The short's cash leg was never reversed and its liability disappeared from
    equity, inflating equity and therefore the next position size.
    """
    closes = [100.0] * 40
    strat = Scripted({26: Signal.SELL, 30: Signal.BUY})
    r = Backtester([strat], {"X": bars(closes)}, spread_bps=0.0,
                   initial_capital=100_000.0,
                   risk_overrides={"stop_loss_pct": 0.99,
                                   "take_profit_pct": 0.99}).run()

    # Exactly one completed trip: the short, closed by the opposing signal.
    assert len(r.trips) == 1
    assert r.trips[0].side == "sell"
    assert r.trips[0].exit_reason == "scripted"
    peak = max(eq for _, eq in r.equity_curve)
    assert peak < 100_000.0 * 1.01


def test_same_direction_signal_does_not_stack_the_position():
    closes = [100.0] * 40
    strat = Scripted({26: Signal.BUY, 30: Signal.BUY, 34: Signal.BUY})
    r = Backtester([strat], {"X": bars(closes)}, spread_bps=0.0,
                   risk_overrides={"stop_loss_pct": 0.99,
                                   "take_profit_pct": 0.99}).run()
    assert len(r.trips) == 1
    assert r.blocked.get("already_in_position", 0) == 2


def test_realised_pnl_equals_cash_change_on_a_mixed_book():
    """The accounting invariant, exercised over many longs and shorts.

    If this drifts, every statistic downstream is fiction. The harness raises
    on drift, so reaching the assertions at all is most of the test.
    """
    closes = [100.0 + 8.0 * math.sin(i / 3.0) for i in range(300)]
    series = bars(closes)
    syms = {"A": series, "B": bars([50.0 + 4.0 * math.cos(i / 4.0)
                                    for i in range(300)], symbol_offset=1)}
    r = Backtester([SMACrossoverStrategy(), RSIStrategy(), GridStrategy()],
                   syms, initial_capital=100_000.0).run()

    final_cash = r.equity_curve[-1][1]
    assert final_cash - r.initial_capital == pytest.approx(r.total_pnl, abs=1e-6)


def test_accounting_drift_is_detected():
    """The invariant must raise, not warn, when a cash leg is wrong.

    Simulated by shorting the exit fill used for cash while leaving the PnL
    calculation intact, which is exactly the shape of the bugs it guards.
    """
    closes = [100.0] * 40
    strat = Scripted({26: Signal.BUY, 30: Signal.SELL})
    bt = Backtester([strat], {"X": bars(closes)}, spread_bps=0.0)

    real_trip = harness.Trip

    def make(*args, **kwargs):
        t = real_trip(*args, **kwargs)
        t.pnl = t.pnl + 1000.0
        return t

    harness.Trip = make
    try:
        with pytest.raises(AssertionError, match="accounting drift"):
            bt.run()
    finally:
        harness.Trip = real_trip


# --------------------------------------------------------------------------
# Risk limits are the live ones
# --------------------------------------------------------------------------
def test_leverage_cap_blocks_overexposure():
    """Ten symbols each sized 20% of equity cannot all fill at 1x leverage."""
    syms = {f"S{i}": bars([100.0] * 40, symbol_offset=i) for i in range(10)}
    r = Backtester([AlwaysBuy()], syms, initial_capital=100_000.0,
                   leverage=1.0,
                   risk_overrides={"max_position_size": 0.20,
                                   "max_open_positions": 10,
                                   "stop_loss_pct": 0.99,
                                   "take_profit_pct": 0.99}).run()
    assert r.blocked.get("exceeds_buying_power", 0) > 0


def test_higher_leverage_allows_more_exposure():
    syms = {f"S{i}": bars([100.0] * 40, symbol_offset=i) for i in range(10)}
    common = dict(initial_capital=100_000.0,
                  risk_overrides={"max_position_size": 0.20,
                                  "max_open_positions": 10,
                                  "stop_loss_pct": 0.99,
                                  "take_profit_pct": 0.99})
    low = Backtester([AlwaysBuy()], syms, leverage=1.0, **common).run()
    high = Backtester([AlwaysBuy()], syms, leverage=3.0, **common).run()
    assert len(high.trips) > len(low.trips)


def test_min_confidence_override_is_respected():
    closes = [100.0] * 40
    strat = Scripted({26: Signal.BUY}, confidence=0.4)
    r = Backtester([strat], {"X": bars(closes)},
                   risk_overrides={"min_confidence": 0.9}).run()
    assert r.trips == []
    assert r.blocked.get("risk_rejected", 0) > 0


def test_max_open_positions_is_enforced():
    syms = {f"S{i}": bars([100.0] * 40, symbol_offset=i) for i in range(8)}
    r = Backtester([AlwaysBuy()], syms, initial_capital=1_000_000.0,
                   leverage=10.0,
                   risk_overrides={"max_open_positions": 3,
                                   "max_position_size": 0.01,
                                   "stop_loss_pct": 0.99,
                                   "take_profit_pct": 0.99}).run()
    # Never more than 3 concurrently open, so at most 3 open at the end.
    assert len([t for t in r.trips if t.exit_reason == "end_of_backtest"]) <= 3


def test_daily_trade_cap_resets_on_a_new_day():
    """Without the rollover the cap latches and later days never trade.

    The position is deliberately closed on day one, otherwise the risk
    manager's duplicate-position guard would block day two and the test would
    pass or fail for the wrong reason.
    """
    day1 = bars([100.0] * 40, start=datetime(2026, 1, 5, 9, 30))
    day2 = bars([100.0] * 40, start=datetime(2026, 1, 6, 9, 30))
    series = day1 + day2

    # index 26 -> buy day 1, 30 -> flat again, 70 -> buy day 2
    strat = Scripted({26: Signal.BUY, 30: Signal.SELL, 70: Signal.BUY})
    r = Backtester([strat], {"X": series}, warmup=25,
                   initial_capital=100_000.0, leverage=5.0,
                   risk_overrides={"max_daily_trades": 1,
                                   "max_position_size": 0.05,
                                   "stop_loss_pct": 0.99,
                                   "take_profit_pct": 0.99}).run()
    days = {t.entry_time.date() for t in r.trips}
    assert days == {datetime(2026, 1, 5).date(), datetime(2026, 1, 6).date()}, \
        f"daily cap did not reset; entries only on {days}"


def test_daily_trade_cap_blocks_a_second_trade_on_the_same_day():
    """Guards the other direction: the cap must actually bind within a day."""
    series = bars([100.0] * 80, start=datetime(2026, 1, 5, 9, 30))
    strat = Scripted({26: Signal.BUY, 30: Signal.SELL, 40: Signal.BUY})
    r = Backtester([strat], {"X": series}, warmup=25,
                   initial_capital=100_000.0, leverage=5.0,
                   risk_overrides={"max_daily_trades": 1,
                                   "max_position_size": 0.05,
                                   "stop_loss_pct": 0.99,
                                   "take_profit_pct": 0.99}).run()
    entries = [t.entry_time for t in r.trips]
    assert len(entries) == 1, f"cap did not bind, got {len(entries)} entries"


def test_unrealistic_override_name_is_rejected():
    with pytest.raises(AttributeError):
        Backtester([], {"X": bars([100.0] * 30)},
                   risk_overrides={"not_a_real_knob": 1}).run()


# --------------------------------------------------------------------------
# Share rounding
# --------------------------------------------------------------------------
def test_whole_share_mode_never_buys_fractions():
    closes = [333.33] * 40
    strat = Scripted({26: Signal.BUY})
    r = Backtester([strat], {"X": bars(closes)}, whole_shares=True).run()
    assert r.trips
    for t in r.trips:
        assert t.quantity == float(int(t.quantity))


def test_fractional_mode_allows_fractions():
    closes = [333.33] * 40
    strat = Scripted({26: Signal.BUY})
    r = Backtester([strat], {"X": bars(closes)}, whole_shares=False).run()
    assert r.trips
    assert any(t.quantity != float(int(t.quantity)) for t in r.trips)


def test_position_too_small_to_buy_one_share_is_blocked():
    closes = [100_000.0] * 40
    strat = Scripted({26: Signal.BUY})
    r = Backtester([strat], {"X": bars(closes)}, initial_capital=1_000.0,
                   whole_shares=True).run()
    assert r.trips == []
    assert r.blocked.get("qty_rounds_to_zero", 0) > 0


# --------------------------------------------------------------------------
# Reported statistics
# --------------------------------------------------------------------------
def test_multi_symbol_timeline_is_chronological():
    a = bars([100.0] * 40, symbol_offset=0)
    b = bars([200.0] * 40, symbol_offset=1)
    bt = Backtester([], {"A": a, "B": b}, warmup=25)
    stamps = [ts for ts, _, _ in bt._timeline()]
    assert stamps == sorted(stamps)


def test_stats_on_a_known_set_of_trips():
    from backtest.harness import BacktestResult, Trip

    def trip(pnl, day):
        t = datetime(2026, 1, day, 10, 0)
        return Trip(symbol="X", side="buy", quantity=1, entry_price=100,
                    exit_price=100 + pnl, entry_time=t, exit_time=t, pnl=pnl,
                    strategy="s", exit_reason="r")

    r = BacktestResult(initial_capital=1000.0, trips=[
        trip(10.0, 5), trip(-4.0, 5), trip(6.0, 6), trip(-2.0, 6),
    ])
    assert r.total_pnl == pytest.approx(10.0)
    assert r.win_rate == pytest.approx(0.5)
    assert r.avg_win == pytest.approx(8.0)
    assert r.avg_loss == pytest.approx(-3.0)
    assert r.profit_factor == pytest.approx(16.0 / 6.0)
    assert r.trading_days == 2
    assert r.daily_pnl[datetime(2026, 1, 5).date()] == pytest.approx(6.0)
    assert r.daily_pnl[datetime(2026, 1, 6).date()] == pytest.approx(4.0)
    assert r.avg_daily_pnl == pytest.approx(5.0)
    assert r.days_hitting(5.0) == 1      # day 5 made 6, day 6 made 4
    assert r.days_hitting(4.0) == 2
    assert r.days_hitting(7.0) == 0
    assert r.pct_days_hitting(5.0) == pytest.approx(0.5)


def test_max_drawdown_measures_peak_to_trough():
    from backtest.harness import BacktestResult

    curve = [(datetime(2026, 1, 5, 9, 30 + i), v)
             for i, v in enumerate([100.0, 120.0, 60.0, 90.0])]
    r = BacktestResult(initial_capital=100.0, equity_curve=curve)
    assert r.max_drawdown == pytest.approx(0.5)  # 120 -> 60


def test_empty_result_stats_do_not_raise():
    from backtest.harness import BacktestResult

    r = BacktestResult(initial_capital=1000.0)
    assert r.total_pnl == 0
    assert r.expectancy == 0
    assert r.win_rate == 0
    assert r.t_stat == 0
    assert r.max_drawdown == 0
    assert r.avg_daily_pnl == 0
    assert r.daily_sharpe == 0
    assert r.pct_days_hitting(400) == 0
    assert r.profit_factor is None


def test_t_stat_flags_a_weak_edge_as_insignificant():
    from backtest.harness import BacktestResult, Trip

    def trip(pnl, i):
        t = datetime(2026, 1, 5, 10, 0) + timedelta(minutes=i)
        return Trip("X", "buy", 1, 100, 100 + pnl, t, t, pnl, "s", "r")

    # Alternating +10 / -9: tiny mean, large spread.
    noisy = BacktestResult(initial_capital=1000.0, trips=[
        trip(10.0 if i % 2 else -9.0, i) for i in range(40)
    ])
    assert abs(noisy.t_stat) < 1.98

    # Consistent +5 with little variance is significant.
    steady = BacktestResult(initial_capital=1000.0, trips=[
        trip(5.0 + (0.1 if i % 2 else -0.1), i) for i in range(40)
    ])
    assert steady.t_stat > 1.98


def test_signals_seen_counts_non_hold_signals():
    closes = [100.0] * 40
    strat = Scripted({26: Signal.BUY, 30: Signal.SELL})
    r = Backtester([strat], {"X": bars(closes)}, spread_bps=0.0).run()
    assert r.signals_seen == 2
