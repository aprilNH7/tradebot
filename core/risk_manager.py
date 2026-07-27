"""Risk Manager — Position sizing, drawdown control, stop-loss enforcement."""

from dataclasses import dataclass, field
from datetime import datetime

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

    @property
    def win_rate(self) -> float:
        return self.winning_trades / self.total_trades if self.total_trades else 0.0

    @property
    def drawdown_pct(self) -> float:
        if self.peak_balance == 0:
            return 0.0
        return (self.peak_balance - self.current_balance) / self.peak_balance


class RiskManager:
    def __init__(self):
        self.max_drawdown = settings.RISK_MAX_DRAWDOWN
        self.max_position_size = settings.RISK_MAX_POSITION_SIZE
        self.stop_loss_pct = settings.RISK_STOP_LOSS_PCT
        self.take_profit_pct = settings.RISK_TAKE_PROFIT_PCT
        self.max_daily_trades = settings.RISK_MAX_DAILY_TRADES
        self.max_open_positions = settings.RISK_MAX_OPEN_POSITIONS
        self.min_confidence = settings.RISK_MIN_CONFIDENCE
        self.metrics = RiskMetrics()
        self._active_positions: dict[str, dict] = {}

    def update_balance(self, balance: float):
        self.metrics.current_balance = balance
        if balance > self.metrics.peak_balance:
            self.metrics.peak_balance = balance
        dd = self.metrics.drawdown_pct
        if dd > self.metrics.max_drawdown:
            self.metrics.max_drawdown = dd

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed based on risk limits."""
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

    def maybe_reset_daily(self) -> bool:
        """Reset daily counters when the calendar day rolls over.

        Without this the daily trade limit is a one-way latch: once the bot
        hits max_daily_trades it stays blocked until the process restarts.
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
            "max_drawdown": f"{self.metrics.max_drawdown:.2%}",
            "open_positions": len(self._active_positions),
            "daily_trades": self.metrics.daily_trades,
        }
