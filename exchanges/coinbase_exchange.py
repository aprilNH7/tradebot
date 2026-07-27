"""Coinbase exchange connector via ccxt."""

from datetime import datetime
from typing import Optional

import ccxt

from config.settings import settings
from exchanges.base import (
    BaseExchange, MarketType, OrderSide, OrderType,
    Ticker, OHLCV, Order, Position,
)
from utils.logger import setup_logger

log = setup_logger("coinbase")


class CoinbaseExchange(BaseExchange):
    market_type = MarketType.CRYPTO

    def __init__(self):
        self.client: Optional[ccxt.coinbase] = None

    def connect(self) -> bool:
        try:
            self.client = ccxt.coinbase({
                "apiKey": settings.COINBASE_API_KEY,
                "secret": settings.COINBASE_SECRET_KEY,
                "enableRateLimit": True,
            })
            self.client.load_markets()
            log.info("Connected to Coinbase")
            return True
        except Exception as e:
            log.error(f"Coinbase connection failed: {e}")
            return False

    def get_ticker(self, symbol: str) -> Ticker:
        t = self.client.fetch_ticker(symbol)
        return Ticker(
            symbol=symbol,
            bid=t["bid"] or 0,
            ask=t["ask"] or 0,
            last=t["last"] or 0,
            volume=t["baseVolume"] or 0,
            timestamp=datetime.now(),
        )

    def get_ohlcv(self, symbol: str, timeframe: str = "1h",
                  limit: int = 100) -> list[OHLCV]:
        raw = self.client.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [
            OHLCV(
                timestamp=datetime.fromtimestamp(c[0] / 1000),
                open=c[1], high=c[2], low=c[3], close=c[4], volume=c[5],
            )
            for c in raw
        ]

    def get_balance(self) -> dict:
        bal = self.client.fetch_balance()
        return {
            asset: {"free": v["free"], "used": v["used"], "total": v["total"]}
            for asset, v in bal.items()
            if isinstance(v, dict) and v.get("total", 0) > 0
        }

    def place_order(self, symbol: str, side: OrderSide,
                    order_type: OrderType, quantity: float,
                    price: Optional[float] = None) -> Order:
        ot = "market" if order_type == OrderType.MARKET else "limit"
        result = self.client.create_order(symbol, ot, side.value, quantity, price)
        log.info(f"Order placed: {side.value} {quantity} {symbol}")
        return Order(
            id=result["id"],
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            filled_price=result.get("average"),
            status=result["status"],
        )

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            self.client.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            log.error(f"Cancel failed: {e}")
            return False

    def get_positions(self) -> list[Position]:
        bal = self.client.fetch_balance()
        positions = []
        for asset, v in bal.items():
            if isinstance(v, dict) and v.get("total", 0) > 0 and asset not in ("USD", "USDT", "USDC"):
                pair = f"{asset}/USD"
                try:
                    ticker = self.get_ticker(pair)
                    positions.append(Position(
                        symbol=pair,
                        side=OrderSide.BUY,
                        quantity=v["total"],
                        entry_price=0,
                        current_price=ticker.last,
                    ))
                except Exception:
                    pass
        return positions

    def get_order_status(self, order_id: str, symbol: str) -> Order:
        o = self.client.fetch_order(order_id, symbol)
        return Order(
            id=o["id"],
            symbol=symbol,
            side=OrderSide(o["side"]),
            order_type=OrderType.MARKET if o["type"] == "market" else OrderType.LIMIT,
            quantity=o["amount"],
            price=o.get("price"),
            filled_price=o.get("average"),
            status=o["status"],
        )
