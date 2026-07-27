"""OANDA forex connector."""

from datetime import datetime
from typing import Optional

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders_ep
import oandapyV20.endpoints.positions as positions_ep
import oandapyV20.endpoints.accounts as accounts_ep
import oandapyV20.endpoints.trades as trades_ep

from config.settings import settings
from exchanges.base import (
    BaseExchange, MarketType, OrderSide, OrderType,
    Ticker, OHLCV, Order, Position,
)
from utils.logger import setup_logger

log = setup_logger("oanda")


class OandaExchange(BaseExchange):
    market_type = MarketType.FOREX

    def __init__(self):
        self.client: Optional[oandapyV20.API] = None
        self.account_id = settings.OANDA_ACCOUNT_ID

    def connect(self) -> bool:
        try:
            env = "practice" if settings.OANDA_ENVIRONMENT == "practice" else "live"
            self.client = oandapyV20.API(
                access_token=settings.OANDA_ACCESS_TOKEN,
                environment=env,
            )
            r = accounts_ep.AccountDetails(self.account_id)
            self.client.request(r)
            balance = r.response["account"]["balance"]
            log.info(f"Connected to OANDA — balance: {balance}")
            return True
        except Exception as e:
            log.error(f"OANDA connection failed: {e}")
            return False

    def get_ticker(self, symbol: str) -> Ticker:
        params = {"instruments": symbol}
        r = instruments.InstrumentsCandles(
            instrument=symbol,
            params={"count": 1, "granularity": "S5"},
        )
        self.client.request(r)
        candle = r.response["candles"][-1]["mid"]
        return Ticker(
            symbol=symbol,
            bid=float(candle["c"]),
            ask=float(candle["c"]),
            last=float(candle["c"]),
            volume=float(r.response["candles"][-1].get("volume", 0)),
            timestamp=datetime.now(),
        )

    def get_ohlcv(self, symbol: str, timeframe: str = "1h",
                  limit: int = 100) -> list[OHLCV]:
        tf_map = {"1m": "M1", "5m": "M5", "15m": "M15",
                  "1h": "H1", "4h": "H4", "1d": "D"}
        gran = tf_map.get(timeframe, "H1")
        r = instruments.InstrumentsCandles(
            instrument=symbol,
            params={"count": limit, "granularity": gran},
        )
        self.client.request(r)
        return [
            OHLCV(
                timestamp=datetime.fromisoformat(c["time"].replace("Z", "+00:00")),
                open=float(c["mid"]["o"]),
                high=float(c["mid"]["h"]),
                low=float(c["mid"]["l"]),
                close=float(c["mid"]["c"]),
                volume=float(c.get("volume", 0)),
            )
            for c in r.response["candles"]
            if c["complete"]
        ]

    def get_balance(self) -> dict:
        r = accounts_ep.AccountDetails(self.account_id)
        self.client.request(r)
        acct = r.response["account"]
        return {
            "balance": float(acct["balance"]),
            "unrealized_pnl": float(acct["unrealizedPL"]),
            "nav": float(acct["NAV"]),
            "margin_used": float(acct["marginUsed"]),
        }

    def place_order(self, symbol: str, side: OrderSide,
                    order_type: OrderType, quantity: float,
                    price: Optional[float] = None) -> Order:
        units = int(quantity) if side == OrderSide.BUY else -int(quantity)
        order_data = {"order": {
            "instrument": symbol,
            "units": str(units),
            "type": "MARKET" if order_type == OrderType.MARKET else "LIMIT",
            "timeInForce": "FOK" if order_type == OrderType.MARKET else "GTC",
        }}
        if price and order_type == OrderType.LIMIT:
            order_data["order"]["price"] = str(price)

        r = orders_ep.OrderCreate(self.account_id, data=order_data)
        self.client.request(r)
        fill = r.response.get("orderFillTransaction", {})
        log.info(f"Order placed: {side.value} {quantity} {symbol}")
        return Order(
            id=fill.get("id", r.response.get("orderCreateTransaction", {}).get("id", "")),
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            filled_price=float(fill["price"]) if "price" in fill else None,
            status="filled" if fill else "pending",
        )

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            r = orders_ep.OrderCancel(self.account_id, orderID=order_id)
            self.client.request(r)
            return True
        except Exception as e:
            log.error(f"Cancel failed: {e}")
            return False

    def get_positions(self) -> list[Position]:
        r = positions_ep.OpenPositions(self.account_id)
        self.client.request(r)
        positions = []
        for p in r.response.get("positions", []):
            long_units = int(p["long"]["units"])
            short_units = int(p["short"]["units"])
            if long_units > 0:
                positions.append(Position(
                    symbol=p["instrument"],
                    side=OrderSide.BUY,
                    quantity=long_units,
                    entry_price=float(p["long"]["averagePrice"]),
                    unrealized_pnl=float(p["long"]["unrealizedPL"]),
                ))
            if short_units < 0:
                positions.append(Position(
                    symbol=p["instrument"],
                    side=OrderSide.SELL,
                    quantity=abs(short_units),
                    entry_price=float(p["short"]["averagePrice"]),
                    unrealized_pnl=float(p["short"]["unrealizedPL"]),
                ))
        return positions

    def get_order_status(self, order_id: str, symbol: str) -> Order:
        r = orders_ep.OrderDetails(self.account_id, orderID=order_id)
        self.client.request(r)
        o = r.response["order"]
        side = OrderSide.BUY if int(o.get("units", 0)) > 0 else OrderSide.SELL
        return Order(
            id=o["id"],
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET if o["type"] == "MARKET" else OrderType.LIMIT,
            quantity=abs(int(o.get("units", 0))),
            price=float(o.get("price", 0)) if o.get("price") else None,
            status=o["state"].lower(),
        )
