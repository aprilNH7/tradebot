"""RSI Strategy — Relative Strength Index overbought/oversold."""

import pandas as pd
import ta

from config.settings import settings
from exchanges.base import OHLCV
from strategies.base import BaseStrategy, Signal, TradeSignal
from utils.logger import setup_logger

log = setup_logger("strategy.rsi")


class RSIStrategy(BaseStrategy):
    name = "rsi"

    def __init__(self, period: int = None, overbought: float = None,
                 oversold: float = None):
        self.period = period or settings.RSI_PERIOD
        self.overbought = overbought or settings.RSI_OVERBOUGHT
        self.oversold = oversold or settings.RSI_OVERSOLD

    def analyze(self, symbol: str, candles: list[OHLCV],
                current_price: float) -> TradeSignal:
        if len(candles) < self.period + 5:
            return TradeSignal(
                signal=Signal.HOLD, symbol=symbol, confidence=0,
                strategy=self.name, reason="Insufficient data",
            )

        df = pd.DataFrame([{"close": c.close} for c in candles])
        df["rsi"] = ta.momentum.rsi(df["close"], self.period)

        current_rsi = df["rsi"].iloc[-1]
        prev_rsi = df["rsi"].iloc[-2]

        # Oversold bounce — BUY signal
        if prev_rsi <= self.oversold and current_rsi > self.oversold:
            confidence = min((self.oversold - prev_rsi) / self.oversold, 1.0)
            return TradeSignal(
                signal=Signal.BUY,
                symbol=symbol,
                confidence=max(confidence, 0.5),
                strategy=self.name,
                reason=f"RSI bounced from oversold: {prev_rsi:.1f} -> {current_rsi:.1f}",
                stop_loss=current_price * (1 - settings.RISK_STOP_LOSS_PCT),
                take_profit=current_price * (1 + settings.RISK_TAKE_PROFIT_PCT),
            )

        # Overbought reversal — SELL signal
        if prev_rsi >= self.overbought and current_rsi < self.overbought:
            confidence = min((prev_rsi - self.overbought) / (100 - self.overbought), 1.0)
            return TradeSignal(
                signal=Signal.SELL,
                symbol=symbol,
                confidence=max(confidence, 0.5),
                strategy=self.name,
                reason=f"RSI dropped from overbought: {prev_rsi:.1f} -> {current_rsi:.1f}",
            )

        # Strong oversold — high confidence BUY
        if current_rsi < self.oversold - 10:
            return TradeSignal(
                signal=Signal.BUY,
                symbol=symbol,
                confidence=0.8,
                strategy=self.name,
                reason=f"RSI deeply oversold: {current_rsi:.1f}",
                stop_loss=current_price * (1 - settings.RISK_STOP_LOSS_PCT * 1.5),
                take_profit=current_price * (1 + settings.RISK_TAKE_PROFIT_PCT * 1.5),
            )

        # No entry condition met. Label the zone so logs distinguish a genuinely
        # neutral RSI from one that is stretched but still awaiting a reversal.
        if current_rsi >= self.overbought:
            zone = f"overbought ({current_rsi:.1f}) — waiting for reversal"
        elif current_rsi <= self.oversold:
            zone = f"oversold ({current_rsi:.1f}) — waiting for bounce"
        else:
            zone = f"neutral ({current_rsi:.1f})"

        return TradeSignal(
            signal=Signal.HOLD, symbol=symbol, confidence=0.0,
            strategy=self.name, reason=f"RSI {zone}",
        )

    def get_params(self) -> dict:
        return {
            "period": self.period,
            "overbought": self.overbought,
            "oversold": self.oversold,
        }
