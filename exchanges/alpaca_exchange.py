"""Alpaca exchange connector for US stocks."""

from datetime import datetime
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

    def get_ohlcv(self, symbol: str, timeframe: str = "1h",
                  limit: int = 100) -> list[OHLCV]:
        tf_map = {"1m": "1Min", "5m": "5Min", "15m": "15Min",
                  "1h": "1Hour", "1d": "1Day"}
        tf = tf_map.get(timeframe, "1Hour")
        bars = self.client.get_bars(symbol, tf, limit=limit).df
        return [
            OHLCV(
                timestamp=idx.to_pydatetime(),
                open=row["open"], high=row["high"],
                low=row["low"], close=row["close"],
                volume=row["volume"],
            )
            for idx, row in bars.iterrows()
        ]

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
        ot = "market" if order_type == OrderType.MARKET else "limit"
        params = {
            "symbol": symbol,
            "qty": int(quantity),
            "side": side.value,
            "type": ot,
            "time_in_force": "day",
        }
        if price and ot == "limit":
            params["limit_price"] = price

        result = self.client.submit_order(**params)
        log.info(f"Order placed: {side.value} {quantity} {symbol}")
        return Order(
            id=result.id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            status=result.status,
        )

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
            status=o.status,
        )
