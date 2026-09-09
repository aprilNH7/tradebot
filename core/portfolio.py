"""Portfolio Manager — Track positions, balances, and performance."""

from dataclasses import dataclass, field
from datetime import datetime

from utils.logger import setup_logger

log = setup_logger("portfolio")


@dataclass
class TradeRecord:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float | None = None
    pnl: float = 0.0
    strategy: str = ""
    market: str = ""
    entry_time: datetime = field(default_factory=datetime.now)
    exit_time: datetime | None = None

    @property
    def is_closed(self) -> bool:
        """True once an exit price has been recorded."""
        return self.exit_price is not None


class Portfolio:
    def __init__(self):
        """Initialise an empty portfolio with no capital or trade history."""
        self.trade_history: list[TradeRecord] = []
        self.balances: dict[str, float] = {}
        self.initial_capital: float = 0.0

    def set_initial_capital(self, amount: float) -> None:
        """Record the starting capital and log it for the audit trail."""
        self.initial_capital = amount
        log.info(f"Initial capital set: ${amount:.2f}")

    def record_trade(self, trade: TradeRecord) -> None:
        """Append a new open trade to the history and log the entry."""
        self.trade_history.append(trade)
        log.info(
            f"Trade recorded: {trade.side} {trade.quantity} {trade.symbol} "
            f"@ ${trade.entry_price:.4f} [{trade.strategy}]"
        )

    def close_trade(self, symbol: str, exit_price: float) -> TradeRecord | None:
        """Mark the most recent open trade for `symbol` as closed.

        Computes realised PnL from the entry price and side, and returns the
        closed trade record. Returns None if no matching open trade exists.
        """
        for trade in reversed(self.trade_history):
            if trade.symbol == symbol and trade.exit_price is None:
                trade.exit_price = exit_price
                trade.exit_time = datetime.now()
                # Case-insensitive: callers pass OrderSide.value ("buy") while
                # the Signal enum uses "BUY".
                if trade.side.strip().lower() == "buy":
                    trade.pnl = (exit_price - trade.entry_price) * trade.quantity
                else:
                    trade.pnl = (trade.entry_price - exit_price) * trade.quantity
                log.info(f"Closed {symbol}: PnL ${trade.pnl:.2f}")
                return trade
        return None

    def get_open_trades(self) -> list[TradeRecord]:
        """Return all trades that have not yet been closed."""
        return [t for t in self.trade_history if t.exit_price is None]

    def get_closed_trades(self) -> list[TradeRecord]:
        """Return all trades that have an exit price recorded."""
        return [t for t in self.trade_history if t.exit_price is not None]

    def get_total_pnl(self) -> float:
        """Return the sum of realised PnL across all closed trades."""
        return sum(t.pnl for t in self.trade_history if t.exit_price is not None)

    def get_performance(self) -> dict:
        """Compute headline performance metrics from closed trades."""
        closed = self.get_closed_trades()
        winners = [t for t in closed if t.pnl > 0]
        losers = [t for t in closed if t.pnl < 0]
        total_pnl = sum(t.pnl for t in closed)

        avg_win = sum(t.pnl for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t.pnl for t in losers) / len(losers) if losers else 0

        return {
            "total_trades": len(closed),
            "open_trades": len(self.get_open_trades()),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": f"{len(winners) / len(closed) * 100:.1f}%" if closed else "N/A",
            "total_pnl": f"${total_pnl:.2f}",
            "avg_win": f"${avg_win:.2f}",
            "avg_loss": f"${avg_loss:.2f}",
            "profit_factor": f"{abs(avg_win / avg_loss):.2f}" if avg_loss != 0 else "N/A",
            "roi": f"{total_pnl / self.initial_capital * 100:.2f}%" if self.initial_capital else "N/A",
        }

    def get_strategy_breakdown(self) -> dict:
        """Aggregate realised PnL, trade count and wins by strategy name."""
        breakdown = {}
        for trade in self.get_closed_trades():
            if trade.strategy not in breakdown:
                breakdown[trade.strategy] = {"trades": 0, "pnl": 0, "wins": 0}
            breakdown[trade.strategy]["trades"] += 1
            breakdown[trade.strategy]["pnl"] += trade.pnl
            if trade.pnl > 0:
                breakdown[trade.strategy]["wins"] += 1
        return breakdown
