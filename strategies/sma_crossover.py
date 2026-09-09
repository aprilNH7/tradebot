"""SMA Crossover Strategy — Fast/Slow moving average crossover."""

import pandas as pd
import ta

from config.settings import settings
from exchanges.base import OHLCV
from strategies.base import BaseStrategy, Signal, TradeSignal
from utils.logger import setup_logger

log = setup_logger("strategy.sma")


class SMACrossoverStrategy(BaseStrategy):
    name = "sma_crossover"

    def __init__(self, fast_period: int = None, slow_period: int = None) -> None:
        self.fast_period = fast_period or settings.SMA_FAST_PERIOD
        self.slow_period = slow_period or settings.SMA_SLOW_PERIOD

    def analyze(self, symbol: str, candles: list[OHLCV],
                current_price: float) -> TradeSignal:
        if len(candles) < self.slow_period + 2:
            return TradeSignal(
                signal=Signal.HOLD, symbol=symbol, confidence=0,
                strategy=self.name, reason="Insufficient data",
            )

        df = pd.DataFrame([
            {"close": c.close, "volume": c.volume} for c in candles
        ])

        df["sma_fast"] = ta.trend.sma_indicator(df["close"], self.fast_period)
        df["sma_slow"] = ta.trend.sma_indicator(df["close"], self.slow_period)

        current_fast = df["sma_fast"].iloc[-1]
        current_slow = df["sma_slow"].iloc[-1]
        prev_fast = df["sma_fast"].iloc[-2]
        prev_slow = df["sma_slow"].iloc[-2]

        # Bullish crossover: fast crosses above slow
        if prev_fast <= prev_slow and current_fast > current_slow:
            spread = (current_fast - current_slow) / current_slow
            confidence = min(spread * 100, 1.0)
            return TradeSignal(
                signal=Signal.BUY,
                symbol=symbol,
                confidence=confidence,
                strategy=self.name,
                reason=f"Bullish crossover: SMA{self.fast_period} crossed above SMA{self.slow_period}",
                stop_loss=current_price * (1 - settings.RISK_STOP_LOSS_PCT),
                take_profit=current_price * (1 + settings.RISK_TAKE_PROFIT_PCT),
            )

        # Bearish crossover: fast crosses below slow
        if prev_fast >= prev_slow and current_fast < current_slow:
            spread = (current_slow - current_fast) / current_slow
            confidence = min(spread * 100, 1.0)
            return TradeSignal(
                signal=Signal.SELL,
                symbol=symbol,
                confidence=confidence,
                strategy=self.name,
                reason=f"Bearish crossover: SMA{self.fast_period} crossed below SMA{self.slow_period}",
            )

        return TradeSignal(
            signal=Signal.HOLD, symbol=symbol, confidence=0.0,
            strategy=self.name, reason="No crossover detected",
        )

    def get_params(self) -> dict:
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
        }
