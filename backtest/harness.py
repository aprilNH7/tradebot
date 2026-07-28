"""Backtest harness — replays historical bars through the real strategy stack.

Fidelity notes, because a backtest that flatters the strategy is worse than none:

* The real `RiskManager` is used, not a reimplementation, so position limits,
  stop-loss, take-profit and the duplicate guard behave exactly as they do live.
  This also means the backtest exercises the same code the live bot trusts.
* `scan_symbol`'s order of operations is replicated exactly: stop-loss, then
  take-profit, then best-signal-by-confidence, then exit-if-held, then sizing.
* No lookahead. At bar `i` a strategy sees `candles[:i+1]` and nothing later.
* Bars from all symbols are merged into one chronological timeline, so the
  open-position cap and daily trade cap bind the same way they do in production.
* Every fill pays the spread adversely. The live measured edge was ~3 basis
  points per round trip, so cost assumptions dominate the result — they are
  explicit and sweepable rather than hidden.
* If a bar's range touches both the stop and the take-profit, the stop is
  assumed to fill first.
"""

import logging
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from core.risk_manager import RiskManager
from exchanges.base import OHLCV
from strategies.base import Signal
from utils.logger import setup_logger

log = setup_logger("backtest")


@dataclass
class Trip:
    """One completed round trip."""
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    strategy: str
    exit_reason: str
    # Excursions as signed fractions of entry price, measured on bar extremes
    # while the position was open. mae <= 0 <= mfe. These are what a stop level
    # should be derived from — a stop tighter than the typical winner's adverse
    # excursion converts winners into losers by construction.
    mae_pct: float = 0.0
    mfe_pct: float = 0.0
    bars_held: int = 0

    @property
    def notional(self) -> float:
        return self.entry_price * self.quantity

    @property
    def return_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        raw = (self.exit_price - self.entry_price) / self.entry_price
        return raw if self.side == "buy" else -raw


@dataclass
class BacktestResult:
    trips: list[Trip] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    initial_capital: float = 0.0
    blocked: dict[str, int] = field(default_factory=dict)
    signals_seen: int = 0
    params: dict = field(default_factory=dict)

    # ---- core stats ----------------------------------------------------
    @property
    def pnls(self) -> list[float]:
        return [t.pnl for t in self.trips]

    @property
    def total_pnl(self) -> float:
        return sum(self.pnls)

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1][1] if self.equity_curve else self.initial_capital

    @property
    def expectancy(self) -> float:
        return statistics.mean(self.pnls) if self.trips else 0.0

    @property
    def win_rate(self) -> float:
        if not self.trips:
            return 0.0
        return len([p for p in self.pnls if p > 0]) / len(self.trips)

    @property
    def avg_win(self) -> float:
        w = [p for p in self.pnls if p > 0]
        return statistics.mean(w) if w else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [p for p in self.pnls if p <= 0]
        return statistics.mean(losses) if losses else 0.0

    @property
    def profit_factor(self) -> Optional[float]:
        gains = sum(p for p in self.pnls if p > 0)
        pain = abs(sum(p for p in self.pnls if p <= 0))
        return gains / pain if pain else None

    @property
    def t_stat(self) -> float:
        """Significance of the mean trip PnL. |t| > 1.98 ~ 95% confidence."""
        if len(self.trips) < 2:
            return 0.0
        sd = statistics.stdev(self.pnls)
        if sd == 0:
            return 0.0
        return self.expectancy / (sd / math.sqrt(len(self.trips)))

    @property
    def max_drawdown(self) -> float:
        """Peak-to-trough decline of the equity curve, as a fraction."""
        peak = -float("inf")
        worst = 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                worst = max(worst, (peak - eq) / peak)
        return worst

    # ---- the question that actually matters ----------------------------
    @property
    def daily_pnl(self) -> dict[date, float]:
        out: dict[date, float] = defaultdict(float)
        for t in self.trips:
            out[t.exit_time.date()] += t.pnl
        return dict(out)

    @property
    def trading_days(self) -> int:
        return len(self.daily_pnl)

    @property
    def avg_daily_pnl(self) -> float:
        d = self.daily_pnl
        return statistics.mean(d.values()) if d else 0.0

    @property
    def daily_sharpe(self) -> float:
        """Annualised Sharpe from daily PnL (risk-free rate assumed 0)."""
        vals = list(self.daily_pnl.values())
        if len(vals) < 2:
            return 0.0
        sd = statistics.stdev(vals)
        if sd == 0:
            return 0.0
        return (statistics.mean(vals) / sd) * math.sqrt(252)

    def days_hitting(self, target: float) -> int:
        return len([v for v in self.daily_pnl.values() if v >= target])

    def pct_days_hitting(self, target: float) -> float:
        d = self.daily_pnl
        return len([v for v in d.values() if v >= target]) / len(d) if d else 0.0

    def by_strategy(self) -> dict[str, dict]:
        agg: dict[str, dict] = {}
        for t in self.trips:
            a = agg.setdefault(t.strategy, {"trips": 0, "pnl": 0.0, "wins": 0})
            a["trips"] += 1
            a["pnl"] += t.pnl
            if t.pnl > 0:
                a["wins"] += 1
        return agg

    def by_exit_reason(self) -> dict[str, dict]:
        agg: dict[str, dict] = {}
        for t in self.trips:
            a = agg.setdefault(t.exit_reason, {"trips": 0, "pnl": 0.0})
            a["trips"] += 1
            a["pnl"] += t.pnl
        return agg


class Backtester:
    """Replays bars through the real strategies and risk manager."""

    def __init__(self, strategies, bars: dict[str, list[OHLCV]],
                 initial_capital: float = 100_000.0,
                 spread_bps: float = 2.0,
                 warmup: int = 25,
                 whole_shares: bool = True,
                 leverage: float = 1.0,
                 honor_signal_levels: bool = False,
                 passive_entries: bool = False,
                 risk_overrides: Optional[dict] = None):
        self.strategies = strategies
        self.bars = bars
        self.initial_capital = initial_capital
        # Half-spread paid on each leg, so a round trip costs ~spread_bps total.
        self.half_spread = (spread_bps / 10_000.0) / 2.0
        self.warmup = warmup
        self.whole_shares = whole_shares
        # Gross notional ceiling as a multiple of equity. 1.0 = unlevered cash
        # account. This is the knob that answers "what leverage does $400/day
        # need", so it must be an explicit input, never an emergent accident.
        self.leverage = leverage
        # When True the harness reads TradeSignal.stop_loss / .take_profit, which
        # the live engine currently ignores entirely.
        self.honor_signal_levels = honor_signal_levels
        # Entries rest on the passive side instead of crossing, so they pay no
        # spread. This is an UPPER BOUND, not a forecast: the harness assumes
        # every passive order fills, whereas live a good share of them will not
        # and those trades simply never happen. Read the delta as "the most this
        # change can be worth", never as expected PnL.
        self.passive_entries = passive_entries
        self.entry_half_spread = 0.0 if passive_entries else self.half_spread
        self.risk_overrides = risk_overrides or {}

    # -- fills ------------------------------------------------------------
    def _buy_fill(self, price: float) -> float:
        return price * (1.0 + self.half_spread)

    def _sell_fill(self, price: float) -> float:
        return price * (1.0 - self.half_spread)

    def _entry_fill(self, price: float, is_buy: bool) -> float:
        """Opening fill. Exits keep crossing because the live bot still uses
        market orders for them — a stop that might not fill is not a stop."""
        edge = self.entry_half_spread
        return price * (1.0 + edge) if is_buy else price * (1.0 - edge)

    def _make_risk_manager(self) -> RiskManager:
        rm = RiskManager()
        for k, v in self.risk_overrides.items():
            if not hasattr(rm, k):
                raise AttributeError(f"RiskManager has no attribute {k!r}")
            setattr(rm, k, v)
        rm.update_balance(self.initial_capital)
        return rm

    def _exit_levels(self, pos: dict, rm) -> tuple[Optional[float], Optional[float]]:
        """Resolve the stop and target price for an open position.

        With `honor_signal_levels` the strategy's own levels win where it gave
        them. Every strategy already fills in `TradeSignal.stop_loss` and
        `.take_profit`, but nothing in the live code ever read those fields, so
        the grid's intent (stop at 2x spacing, target the next grid level) was
        silently replaced by the global 2%/4% defaults.
        """
        is_long = pos["side"] == "buy"
        entry = pos["entry"]

        if self.honor_signal_levels:
            stop_px = pos.get("sig_stop")
            tp_px = pos.get("sig_tp")
            # A level on the wrong side of entry is a strategy bug, not a stop;
            # fall back rather than exit instantly at a nonsensical price.
            if stop_px is not None and (
                (is_long and stop_px >= entry) or (not is_long and stop_px <= entry)
            ):
                stop_px = None
            if tp_px is not None and (
                (is_long and tp_px <= entry) or (not is_long and tp_px >= entry)
            ):
                tp_px = None
            if stop_px is None:
                stop_px = (entry * (1 - rm.stop_loss_pct) if is_long
                           else entry * (1 + rm.stop_loss_pct))
            if tp_px is None:
                tp_px = (entry * (1 + rm.take_profit_pct) if is_long
                         else entry * (1 - rm.take_profit_pct))
            return stop_px, tp_px

        sl, tp = rm.stop_loss_pct, rm.take_profit_pct
        stop_px = entry * (1 - sl) if is_long else entry * (1 + sl)
        tp_px = entry * (1 + tp) if is_long else entry * (1 - tp)
        return stop_px, tp_px

    def _timeline(self) -> list[tuple[datetime, str, int]]:
        """All (timestamp, symbol, bar index) events in chronological order."""
        events = []
        for sym, candles in self.bars.items():
            for i in range(self.warmup, len(candles)):
                events.append((candles[i].timestamp, sym, i))
        events.sort(key=lambda e: e[0])
        return events

    def run(self) -> BacktestResult:
        """Replay the bars. Quiets the live per-trade loggers while running.

        The risk manager logs every close and every block at INFO, which is
        useful live but buries the backtest report under tens of thousands of
        lines.
        """
        noisy = [logging.getLogger(n) for n in ("risk_manager", "strategy")]
        previous = [lg.level for lg in noisy]
        for lg in noisy:
            lg.setLevel(logging.ERROR)
        try:
            return self._run()
        finally:
            for lg, lvl in zip(noisy, previous):
                lg.setLevel(lvl)

    def _run(self) -> BacktestResult:
        rm = self._make_risk_manager()
        result = BacktestResult(
            initial_capital=self.initial_capital,
            params={
                "spread_bps": self.half_spread * 2 * 10_000,
                "warmup": self.warmup,
                "whole_shares": self.whole_shares,
                "leverage": self.leverage,
                "honor_signal_levels": self.honor_signal_levels,
                "passive_entries": self.passive_entries,
                **self.risk_overrides,
            },
        )
        blocked: dict[str, int] = defaultdict(int)
        timeline = self._timeline()

        cash = self.initial_capital
        # symbol -> dict(qty, entry, side, entry_time, strategy)
        open_pos: dict[str, dict] = {}
        last_price: dict[str, float] = {}
        last_day: Optional[date] = None

        def equity() -> float:
            """Cash plus mark-to-market of open positions.

            Short proceeds are already inside `cash` when the position opens, so
            a short's marked value is a pure liability of -px*qty. Adding the
            proceeds again here inflates equity, which inflates the next
            position size, which inflates equity — an exponential feedback loop
            that makes a losing strategy look like it prints trillions.
            """
            mtm = 0.0
            for s, p in open_pos.items():
                px = last_price.get(s, p["entry"])
                if p["side"] == "buy":
                    mtm += px * p["qty"]
                else:
                    mtm -= px * p["qty"]
            return cash + mtm

        def gross_exposure() -> float:
            return sum(
                last_price.get(s, p["entry"]) * p["qty"] for s, p in open_pos.items()
            )

        def close(symbol: str, price: float, when: datetime, reason: str):
            nonlocal cash
            pos = open_pos.pop(symbol, None)
            if pos is None:
                return
            if pos["side"] == "buy":
                fill = self._sell_fill(price)
                pnl = (fill - pos["entry"]) * pos["qty"]
                cash += fill * pos["qty"]
            else:
                fill = self._buy_fill(price)
                pnl = (pos["entry"] - fill) * pos["qty"]
                cash -= fill * pos["qty"]
            rm.close_position(symbol, fill)
            result.trips.append(Trip(
                symbol=symbol, side=pos["side"], quantity=pos["qty"],
                entry_price=pos["entry"], exit_price=fill,
                entry_time=pos["entry_time"], exit_time=when,
                pnl=pnl, strategy=pos["strategy"], exit_reason=reason,
                mae_pct=pos["mae"], mfe_pct=pos["mfe"],
                bars_held=pos["bars"],
            ))

        for ts, sym, i in timeline:
            candles = self.bars[sym]
            bar = candles[i]
            price = bar.close
            last_price[sym] = price

            # Daily counter rollover, same as the live loop.
            if last_day is None or ts.date() > last_day:
                if last_day is not None:
                    rm.reset_daily()
                last_day = ts.date()

            # Mark the book every bar. The live loop feeds equity to the risk
            # manager each cycle, and the drawdown circuit breaker reads it — so
            # the backtest must do the same or it silently skips the kill switch.
            eq_now = equity()
            rm.update_balance(eq_now)
            result.equity_curve.append((ts, eq_now))

            # --- track excursions before deciding anything ---------------
            if sym in open_pos:
                pos = open_pos[sym]
                entry = pos["entry"]
                pos["bars"] += 1
                if entry > 0:
                    if pos["side"] == "buy":
                        adverse = (bar.low - entry) / entry
                        favorable = (bar.high - entry) / entry
                    else:
                        adverse = (entry - bar.high) / entry
                        favorable = (entry - bar.low) / entry
                    pos["mae"] = min(pos["mae"], adverse)
                    pos["mfe"] = max(pos["mfe"], favorable)

            # --- intrabar stop / take-profit on an open position ---------
            if sym in open_pos:
                pos = open_pos[sym]
                is_long = pos["side"] == "buy"
                entry = pos["entry"]
                stop_px, tp_px = self._exit_levels(pos, rm)

                hit_stop = (bar.low <= stop_px if is_long
                            else bar.high >= stop_px) if stop_px else False
                hit_tp = (bar.high >= tp_px if is_long
                          else bar.low <= tp_px) if tp_px else False

                # Conservative: if the bar spans both, assume the stop filled.
                if hit_stop:
                    close(sym, stop_px, ts, "stop_loss")
                    continue
                if hit_tp:
                    close(sym, tp_px, ts, "take_profit")
                    continue

            # --- strategies (no lookahead) -------------------------------
            window = candles[: i + 1]
            best = None
            for strat in self.strategies:
                try:
                    sig = strat.analyze(sym, window, price)
                except Exception as e:
                    log.debug(f"{sym}: {strat.name} raised {e}")
                    continue
                if sig.signal != Signal.HOLD:
                    if best is None or sig.confidence > best.confidence:
                        best = sig
            if best is None:
                continue
            result.signals_seen += 1

            # --- an open position is exited, never silently replaced ------
            # A BUY signal against an open short (or vice versa) used to fall
            # through to the entry block and overwrite open_pos[sym]. That
            # orphaned the old position: its cash leg was never reversed and its
            # mark-to-market liability vanished from equity, which inflated
            # equity, which inflated the next position size. Same runaway as the
            # short-proceeds bug, just harder to see.
            if sym in open_pos:
                held_long = open_pos[sym]["side"] == "buy"
                signal_long = best.signal == Signal.BUY
                if held_long != signal_long:
                    # An opposing market order flattens the position. We close
                    # and wait for a fresh signal rather than modelling a
                    # partial flip in the same bar.
                    close(sym, price, ts, best.strategy)
                else:
                    blocked["already_in_position"] += 1
                continue

            # --- size and enter -----------------------------------------
            eq = equity()
            if eq <= 0:
                blocked["account_blown"] += 1
                continue
            approved, size = rm.evaluate_signal(best, eq)
            if not approved:
                blocked["risk_rejected"] += 1
                continue

            fill = self._entry_fill(price, best.signal == Signal.BUY)
            qty = size / fill if fill > 0 else 0.0
            if self.whole_shares:
                qty = float(int(qty))
            if qty <= 0:
                blocked["qty_rounds_to_zero"] += 1
                continue

            # Gross notional ceiling. Applies to longs and shorts alike: a short
            # consumes buying power even though it credits cash.
            if gross_exposure() + fill * qty > eq * self.leverage:
                blocked["exceeds_buying_power"] += 1
                continue

            side = "buy" if best.signal == Signal.BUY else "sell"
            if side == "buy":
                cash -= fill * qty
            else:
                cash += fill * qty

            # Overwriting an open position leaks its cash leg and its liability.
            # Fail loudly rather than quietly corrupting every later number.
            if sym in open_pos:
                raise AssertionError(
                    f"would overwrite open position in {sym} at {ts}"
                )
            open_pos[sym] = {
                "qty": qty, "entry": fill, "side": side,
                "entry_time": ts, "strategy": best.strategy,
                "sig_stop": best.stop_loss, "sig_tp": best.take_profit,
                "mae": 0.0, "mfe": 0.0, "bars": 0,
            }
            rm.register_trade(sym, side, qty, fill)

        # Mark remaining positions out at their last seen price.
        final_ts = timeline[-1][0] if timeline else datetime.now()
        for sym in list(open_pos):
            close(sym, last_price.get(sym, open_pos[sym]["entry"]),
                  final_ts, "end_of_backtest")

        # Hard accounting invariant. Every leg moves cash by exactly the amount
        # it contributes to a trip's PnL, so with a flat book the realised PnL
        # must equal the change in cash. Both runaway-equity bugs violated this
        # immediately; without the check they surfaced only as absurd totals.
        drift = (cash - self.initial_capital) - result.total_pnl
        tolerance = max(1e-6, abs(self.initial_capital) * 1e-9)
        if abs(drift) > tolerance:
            raise AssertionError(
                f"backtest accounting drift ${drift:,.6f}: cash moved "
                f"${cash - self.initial_capital:,.2f} but trips report "
                f"${result.total_pnl:,.2f}"
            )

        result.equity_curve.append((final_ts, cash))
        result.blocked = dict(blocked)
        return result
