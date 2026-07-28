"""Risk Manager — Position sizing, drawdown control, stop-loss enforcement."""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

from config.settings import settings
from strategies.base import TradeSignal, Signal
from utils.logger import setup_logger

log = setup_logger("risk_manager")


@dataclass
class RiskMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_balance: float = 0.0
    current_balance: float = 0.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    last_reset: datetime = field(default_factory=datetime.now)
    # Equity at the start of the current trading day. The daily loss cap is
    # measured against this rather than against realised PnL so that an open
    # position bleeding out counts against the cap immediately.
    day_start_balance: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.winning_trades / self.total_trades if self.total_trades else 0.0

    @property
    def drawdown_pct(self) -> float:
        if self.peak_balance == 0:
            return 0.0
        return (self.peak_balance - self.current_balance) / self.peak_balance

    @property
    def daily_loss(self) -> float:
        """Dollars lost today — mark-to-market, never negative.

        Prefers the equity delta because that includes unrealised losses. Falls
        back to realised daily PnL when no balance feed has reported yet (the
        backtest harness and unit tests run this way), so the cap still binds
        instead of silently reading zero.
        """
        if self.day_start_balance > 0 and self.current_balance > 0:
            return max(0.0, self.day_start_balance - self.current_balance)
        return max(0.0, -self.daily_pnl)


class RiskManager:
    def __init__(self):
        self.max_drawdown = settings.RISK_MAX_DRAWDOWN
        self.max_position_size = settings.RISK_MAX_POSITION_SIZE
        self.stop_loss_pct = settings.RISK_STOP_LOSS_PCT
        self.take_profit_pct = settings.RISK_TAKE_PROFIT_PCT
        self.max_daily_trades = settings.RISK_MAX_DAILY_TRADES
        self.max_open_positions = settings.RISK_MAX_OPEN_POSITIONS
        self.min_confidence = settings.RISK_MIN_CONFIDENCE
        self.max_daily_loss = settings.RISK_MAX_DAILY_LOSS
        self.metrics = RiskMetrics()
        self._active_positions: dict[str, dict] = {}
        # Date the kill-switch latched. Kept as a date, not a bool, so a stale
        # halt cannot survive into the next session.
        self._halted_on: Optional[date] = None

    def update_balance(self, balance: float):
        self.metrics.current_balance = balance
        if balance > self.metrics.peak_balance:
            self.metrics.peak_balance = balance
        # Anchor the day's opening equity on the first reading we ever get.
        if self.metrics.day_start_balance <= 0:
            self.metrics.day_start_balance = balance
        dd = self.metrics.drawdown_pct
        if dd > self.metrics.max_drawdown:
            self.metrics.max_drawdown = dd
        # Evaluate the cap on every equity refresh so the halt latches the moment
        # the floor is breached, not on the next signal.
        self._check_daily_loss()

    def _check_daily_loss(self) -> bool:
        """Latch the kill-switch if today's loss has reached the cap.

        Latching matters: without it a position that bounces back above the
        threshold would silently re-enable trading on the same day that already
        proved the strategy was wrong.
        """
        if self.max_daily_loss <= 0 or self.is_halted:
            return self.is_halted

        loss = self.metrics.daily_loss
        if loss >= self.max_daily_loss:
            self._halted_on = datetime.now().date()
            log.error(
                f"KILL-SWITCH TRIPPED — down ${loss:,.2f} today "
                f"(cap ${self.max_daily_loss:,.2f}). No new positions until "
                f"the next trading day. Open positions stay stop-managed."
            )
            return True
        return False

    @property
    def is_halted(self) -> bool:
        """True while the daily kill-switch is latched for the current day."""
        return self._halted_on is not None and self._halted_on >= datetime.now().date()

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed based on risk limits."""
        # Daily loss cap — checked first because it is the hardest floor.
        if self._check_daily_loss():
            msg = (
                f"Daily loss cap hit: ${self.metrics.daily_loss:,.2f} "
                f">= ${self.max_daily_loss:,.2f} — halted for the day"
            )
            return False, msg

        # Drawdown check
        if self.metrics.drawdown_pct >= self.max_drawdown:
            msg = f"Max drawdown reached: {self.metrics.drawdown_pct:.2%} >= {self.max_drawdown:.2%}"
            log.warning(msg)
            return False, msg

        # Daily trade limit
        if self.metrics.daily_trades >= self.max_daily_trades:
            msg = f"Daily trade limit reached: {self.metrics.daily_trades}"
            log.warning(msg)
            return False, msg

        # Open positions limit
        if len(self._active_positions) >= self.max_open_positions:
            msg = f"Max open positions: {len(self._active_positions)}"
            log.warning(msg)
            return False, msg

        return True, "OK"

    def evaluate_signal(self, signal: TradeSignal,
                        portfolio_value: float) -> tuple[bool, float]:
        """Evaluate a trade signal and return (approved, position_size)."""
        if signal.signal == Signal.HOLD:
            return False, 0

        can, reason = self.can_trade()
        if not can:
            log.info(f"Trade blocked: {reason}")
            return False, 0

        # Minimum confidence threshold
        if signal.confidence < self.min_confidence:
            log.debug(f"Low confidence: {signal.confidence:.2f}")
            return False, 0

        # Position sizing: Kelly-inspired with confidence scaling
        base_size = portfolio_value * self.max_position_size
        adjusted_size = base_size * signal.confidence

        # Don't add to existing position in same direction
        existing = self._active_positions.get(signal.symbol)
        if existing:
            if existing["side"] == self._norm_side(signal.signal.value):
                log.debug(f"Already in {signal.signal.value} position for {signal.symbol}")
                return False, 0

        return True, adjusted_size

    def register_trade(self, symbol: str, side: str, quantity: float,
                       entry_price: float, count_toward_limits: bool = True):
        self._active_positions[symbol] = {
            # Normalised because callers pass either OrderSide.value ("buy")
            # or Signal.value ("BUY") — comparing raw values silently fails.
            "side": self._norm_side(side),
            "quantity": quantity,
            "entry_price": entry_price,
            "timestamp": datetime.now(),
        }
        # Positions adopted from the exchange on startup were not opened by this
        # session, so they must not consume the daily trade budget.
        if count_toward_limits:
            self.metrics.daily_trades += 1
            self.metrics.total_trades += 1

    @staticmethod
    def _norm_side(side: str) -> str:
        return side.strip().lower()

    def _is_long(self, pos: dict) -> bool:
        return pos["side"] == "buy"

    def close_position(self, symbol: str, exit_price: float):
        pos = self._active_positions.pop(symbol, None)
        if not pos:
            return
        if self._is_long(pos):
            pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["quantity"]

        self.metrics.total_pnl += pnl
        self.metrics.daily_pnl += pnl
        if pnl > 0:
            self.metrics.winning_trades += 1
        else:
            self.metrics.losing_trades += 1
        log.info(f"Closed {symbol}: PnL ${pnl:.2f}")
        # Realising a loss can be what breaches the cap, and there may be no
        # equity refresh until the next cycle. Re-check now so the halt is not
        # delayed by a whole scan interval.
        self._check_daily_loss()

    def get_position(self, symbol: str) -> dict | None:
        """Return the tracked open position for a symbol, if any."""
        return self._active_positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._active_positions

    def check_stop_loss(self, symbol: str, current_price: float) -> bool:
        """Returns True if stop-loss hit and position should be closed."""
        pos = self._active_positions.get(symbol)
        if not pos:
            return False

        if self._is_long(pos):
            loss_pct = (pos["entry_price"] - current_price) / pos["entry_price"]
        else:
            loss_pct = (current_price - pos["entry_price"]) / pos["entry_price"]

        if loss_pct >= self.stop_loss_pct:
            log.warning(f"Stop-loss hit for {symbol}: {loss_pct:.2%} loss")
            return True
        return False

    def check_take_profit(self, symbol: str, current_price: float) -> bool:
        """Returns True if take-profit hit."""
        pos = self._active_positions.get(symbol)
        if not pos:
            return False

        if self._is_long(pos):
            gain_pct = (current_price - pos["entry_price"]) / pos["entry_price"]
        else:
            gain_pct = (pos["entry_price"] - current_price) / pos["entry_price"]

        if gain_pct >= self.take_profit_pct:
            log.info(f"Take-profit hit for {symbol}: {gain_pct:.2%} gain")
            return True
        return False

    def reset_daily(self):
        self.metrics.daily_pnl = 0.0
        self.metrics.daily_trades = 0
        self.metrics.last_reset = datetime.now()
        # Re-anchor the day's opening equity, otherwise yesterday's losses keep
        # counting against today's cap and the bot never trades again.
        self.metrics.day_start_balance = self.metrics.current_balance
        if self._halted_on is not None:
            log.info("New day — daily loss kill-switch released")
        self._halted_on = None

    def maybe_reset_daily(self) -> bool:
        """Reset daily counters when the calendar day rolls over.

        Without this the daily trade limit is a one-way latch: once the bot
        hits max_daily_trades it stays blocked until the process restarts.
        The same applies to the daily loss kill-switch.
        """
        if datetime.now().date() > self.metrics.last_reset.date():
            log.info(
                f"New trading day — resetting daily counters "
                f"(prev: {self.metrics.daily_trades} trades, "
                f"${self.metrics.daily_pnl:.2f} PnL)"
            )
            self.reset_daily()
            return True
        return False

    def get_status(self) -> dict:
        return {
            "total_trades": self.metrics.total_trades,
            "win_rate": f"{self.metrics.win_rate:.1%}",
            "total_pnl": f"${self.metrics.total_pnl:.2f}",
            "daily_pnl": f"${self.metrics.daily_pnl:.2f}",
            "daily_loss": f"${self.metrics.daily_loss:.2f}",
            "daily_loss_cap": f"${self.max_daily_loss:.2f}",
            "halted": self.is_halted,
            "max_drawdown": f"{self.metrics.max_drawdown:.2%}",
            "open_positions": len(self._active_positions),
            "daily_trades": self.metrics.daily_trades,
        }
