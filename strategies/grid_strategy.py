"""Grid Trading Strategy — Place buy/sell orders at fixed intervals."""

from config.settings import settings
from exchanges.base import OHLCV
from strategies.base import BaseStrategy, Signal, TradeSignal
from utils.logger import setup_logger

log = setup_logger("strategy.grid")


class GridStrategy(BaseStrategy):
    name = "grid"

    def __init__(self, levels: int = None, spacing_pct: float = None) -> None:
        self.levels = levels or settings.GRID_LEVELS
        self.spacing_pct = spacing_pct or settings.GRID_SPACING_PCT
        self.grid_prices: dict[str, list[float]] = {}
        self.last_action: dict[str, str] = {}

    def _build_grid(self, symbol: str, center_price: float) -> list[float]:
        grid = []
        for i in range(-self.levels // 2, self.levels // 2 + 1):
            grid.append(center_price * (1 + i * self.spacing_pct))
        self.grid_prices[symbol] = sorted(grid)
        return self.grid_prices[symbol]

    def _find_nearest_levels(self, price: float,
                             grid: list[float]) -> tuple[float | None, float | None]:
        """Bracket `price` between grid levels.

        Returns (below, above); either side is None when the price has moved
        outside the grid. Clamping both sides to the same boundary level would
        make the buy and sell checks indistinguishable — and since the buy
        branch is tested first, a price just above the top of the grid would
        fire a BUY at the highest level.
        """
        below = [g for g in grid if g < price]
        above = [g for g in grid if g > price]
        return (below[-1] if below else None), (above[0] if above else None)

    def analyze(self, symbol: str, candles: list[OHLCV],
                current_price: float) -> TradeSignal:
        if len(candles) < 20:
            return TradeSignal(
                signal=Signal.HOLD, symbol=symbol, confidence=0,
                strategy=self.name, reason="Insufficient data",
            )

        # Build or update grid centered on 20-period average
        avg_price = sum(c.close for c in candles[-20:]) / 20
        grid = self._build_grid(symbol, avg_price)

        buy_level, sell_level = self._find_nearest_levels(current_price, grid)
        last = self.last_action.get(symbol, "none")

        # Price has left the grid — it re-centers on the moving average next
        # cycle, so wait rather than trading against an invalid level.
        if buy_level is None or sell_level is None:
            edge = "below" if buy_level is None else "above"
            return TradeSignal(
                signal=Signal.HOLD, symbol=symbol, confidence=0.0,
                strategy=self.name,
                reason=(
                    f"Price ${current_price:.4f} is {edge} the grid "
                    f"(${grid[0]:.4f}–${grid[-1]:.4f}) — waiting for re-centering"
                ),
            )

        # Price hit buy level
        proximity_buy = abs(current_price - buy_level) / current_price
        proximity_sell = abs(current_price - sell_level) / current_price

        if proximity_buy < self.spacing_pct * 0.3 and last != "buy":
            self.last_action[symbol] = "buy"
            return TradeSignal(
                signal=Signal.BUY,
                symbol=symbol,
                confidence=0.7,
                strategy=self.name,
                reason=f"Price near grid buy level ${buy_level:.4f}",
                stop_loss=buy_level * (1 - self.spacing_pct * 2),
                take_profit=sell_level,
            )

        if proximity_sell < self.spacing_pct * 0.3 and last != "sell":
            self.last_action[symbol] = "sell"
            return TradeSignal(
                signal=Signal.SELL,
                symbol=symbol,
                confidence=0.7,
                strategy=self.name,
                reason=f"Price near grid sell level ${sell_level:.4f}",
            )

        return TradeSignal(
            signal=Signal.HOLD, symbol=symbol, confidence=0.0,
            strategy=self.name,
            reason=f"Between grid levels: buy@{buy_level:.4f} sell@{sell_level:.4f}",
        )

    def get_params(self) -> dict:
        return {
            "levels": self.levels,
            "spacing_pct": self.spacing_pct,
        }
