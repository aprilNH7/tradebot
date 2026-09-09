"""Cross-Exchange Arbitrage Strategy — Exploit price differences."""

from config.settings import settings
from exchanges.base import BaseExchange, OHLCV
from strategies.base import BaseStrategy, Signal, TradeSignal
from utils.logger import setup_logger

log = setup_logger("strategy.arbitrage")


class ArbitrageStrategy(BaseStrategy):
    name = "arbitrage"

    def __init__(self, min_spread_pct: float = None,
                 exchanges: list[BaseExchange] = None) -> None:
        self.min_spread_pct = (
            min_spread_pct if min_spread_pct is not None
            else settings.ARBITRAGE_MIN_SPREAD_PCT
        )
        self.exchanges = exchanges or []
        self._price_cache: dict[str, dict[str, float]] = {}

    def update_prices(self, symbol: str):
        """Fetch prices from all connected exchanges."""
        self._price_cache[symbol] = {}
        for exchange in self.exchanges:
            try:
                ticker = exchange.get_ticker(symbol)
                name = exchange.__class__.__name__
                self._price_cache[symbol][name] = {
                    "bid": ticker.bid,
                    "ask": ticker.ask,
                    "last": ticker.last,
                }
            except Exception as e:
                log.debug(f"Could not fetch {symbol} from {exchange.__class__.__name__}: {e}")

    def find_opportunity(self, symbol: str) -> dict | None:
        """Find the best arbitrage opportunity across exchanges."""
        prices = self._price_cache.get(symbol, {})
        if len(prices) < 2:
            return None

        best_bid_exchange = max(prices, key=lambda x: prices[x]["bid"])
        best_ask_exchange = min(prices, key=lambda x: prices[x]["ask"])

        if best_bid_exchange == best_ask_exchange:
            return None

        best_bid = prices[best_bid_exchange]["bid"]
        best_ask = prices[best_ask_exchange]["ask"]
        spread_pct = (best_bid - best_ask) / best_ask

        if spread_pct > self.min_spread_pct:
            return {
                "buy_exchange": best_ask_exchange,
                "sell_exchange": best_bid_exchange,
                "buy_price": best_ask,
                "sell_price": best_bid,
                "spread_pct": spread_pct,
            }
        return None

    def analyze(self, symbol: str, candles: list[OHLCV],
                current_price: float) -> TradeSignal:
        self.update_prices(symbol)
        opportunity = self.find_opportunity(symbol)

        if opportunity:
            log.info(
                f"Arbitrage opportunity: buy {symbol} on {opportunity['buy_exchange']} "
                f"@ {opportunity['buy_price']:.4f}, sell on {opportunity['sell_exchange']} "
                f"@ {opportunity['sell_price']:.4f} — spread: {opportunity['spread_pct']:.4%}"
            )
            return TradeSignal(
                signal=Signal.BUY,
                symbol=symbol,
                confidence=min(opportunity["spread_pct"] * 50, 1.0),
                strategy=self.name,
                reason=(
                    f"Arbitrage: buy@{opportunity['buy_exchange']} "
                    f"${opportunity['buy_price']:.4f} → sell@{opportunity['sell_exchange']} "
                    f"${opportunity['sell_price']:.4f} ({opportunity['spread_pct']:.4%} spread)"
                ),
            )

        return TradeSignal(
            signal=Signal.HOLD, symbol=symbol, confidence=0.0,
            strategy=self.name, reason="No arbitrage opportunity found",
        )

    def get_params(self) -> dict:
        return {
            "min_spread_pct": self.min_spread_pct,
            "exchanges": [e.__class__.__name__ for e in self.exchanges],
        }
