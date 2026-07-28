"""Alpaca exchange connector for US stocks."""

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import alpaca_trade_api as tradeapi

from config.settings import settings
from exchanges.base import (
    BaseExchange, MarketType, OrderSide, OrderType,
    Ticker, OHLCV, Order, Position,
)
from utils.logger import setup_logger

log = setup_logger("alpaca")


class AlpacaExchange(BaseExchange):
    market_type = MarketType.STOCK

    def __init__(self):
        self.client: Optional[tradeapi.REST] = None

    def connect(self) -> bool:
        try:
            self.client = tradeapi.REST(
                key_id=settings.ALPACA_API_KEY,
                secret_key=settings.ALPACA_SECRET_KEY,
                base_url=settings.ALPACA_BASE_URL,
            )
            account = self.client.get_account()
            log.info(f"Connected to Alpaca — equity: ${account.equity}")
            return True
        except Exception as e:
            log.error(f"Alpaca connection failed: {e}")
            return False

    def get_ticker(self, symbol: str) -> Ticker:
        quote = self.client.get_latest_quote(symbol)
        trade = self.client.get_latest_trade(symbol)
        return Ticker(
            symbol=symbol,
            bid=float(quote.bp),
            ask=float(quote.ap),
            last=float(trade.p),
            volume=float(trade.s),
            timestamp=datetime.now(),
        )

    # Approximate bars produced per trading day, used to size the lookback
    # window so we always come back with enough history for the indicators.
    _BARS_PER_DAY = {"1Min": 390, "5Min": 78, "15Min": 26, "1Hour": 7, "1Day": 1}

    def get_ohlcv(self, symbol: str, timeframe: str = "1h",
                  limit: int = 100) -> list[OHLCV]:
        tf_map = {"1m": "1Min", "5m": "5Min", "15m": "15Min",
                  "1h": "1Hour", "1d": "1Day"}
        tf = tf_map.get(timeframe, "1Hour")

        # Without an explicit start, Alpaca only returns the current day —
        # roughly 7 hourly bars, far short of what a 21-period SMA needs.
        per_day = self._BARS_PER_DAY.get(tf, 7)
        trading_days = math.ceil(limit / per_day)
        # ~1.45x for weekends plus padding for holidays and half-days
        lookback = math.ceil(trading_days * 1.45) + 5
        start = (datetime.now(timezone.utc) - timedelta(days=lookback)).date()

        bars = self.client.get_bars(symbol, tf, start=start.isoformat()).df
        if bars.empty:
            log.warning(f"No bars returned for {symbol} ({tf} since {start})")
            return []

        # get_bars fills forward from `start`, so trim to the newest candles
        bars = bars.tail(limit)
        candles = [
            OHLCV(
                timestamp=idx.to_pydatetime(),
                open=row["open"], high=row["high"],
                low=row["low"], close=row["close"],
                volume=row["volume"],
            )
            for idx, row in bars.iterrows()
        ]
        if len(candles) < limit:
            log.debug(f"{symbol}: {len(candles)}/{limit} candles available")
        return candles

    def get_balance(self) -> dict:
        account = self.client.get_account()
        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "portfolio_value": float(account.portfolio_value),
        }

    def place_order(self, symbol: str, side: OrderSide,
                    order_type: OrderType, quantity: float,
                    price: Optional[float] = None) -> Order:
        qty = int(quantity)
        if qty < 1:
            raise ValueError(
                f"{symbol}: quantity {quantity:.4f} rounds to 0 whole shares"
            )

        ot = "market" if order_type == OrderType.MARKET else "limit"
        params = {
            "symbol": symbol,
            "qty": qty,
            "side": side.value,
            "type": ot,
            "time_in_force": "day",
        }
        if price and ot == "limit":
            params["limit_price"] = price

        result = self.client.submit_order(**params)
        log.info(f"Order placed: {side.value} {qty} {symbol}")
        return Order(
            id=result.id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=float(qty),
            price=price,
            status=result.status,
        )

    def is_market_open(self) -> bool:
        """Query Alpaca's clock so we don't trade on stale closed-market data."""
        try:
            return bool(self.client.get_clock().is_open)
        except Exception as e:
            log.warning(f"Market clock check failed, assuming closed: {e}")
            return False

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            self.client.cancel_order(order_id)
            return True
        except Exception as e:
            log.error(f"Cancel failed: {e}")
            return False

    def get_positions(self) -> list[Position]:
        raw = self.client.list_positions()
        return [
            Position(
                symbol=p.symbol,
                side=OrderSide.BUY if p.side == "long" else OrderSide.SELL,
                quantity=float(p.qty),
                entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                unrealized_pnl=float(p.unrealized_pl),
            )
            for p in raw
        ]

    def get_order_status(self, order_id: str, symbol: str) -> Order:
        o = self.client.get_order(order_id)
        return Order(
            id=o.id,
            symbol=symbol,
            side=OrderSide.BUY if o.side == "buy" else OrderSide.SELL,
            order_type=OrderType.MARKET if o.type == "market" else OrderType.LIMIT,
            quantity=float(o.qty),
            price=float(o.limit_price) if o.limit_price else None,
            filled_price=float(o.filled_avg_price) if o.filled_avg_price else None,
            filled_quantity=float(o.filled_qty) if o.filled_qty is not None else None,
            status=o.status,
        )
