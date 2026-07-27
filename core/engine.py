"""Trading Engine — Main orchestrator that ties everything together."""

import time
from datetime import datetime
from typing import Optional

from exchanges.base import BaseExchange, OrderSide, OrderType, MarketType
from strategies.base import BaseStrategy, Signal
from core.risk_manager import RiskManager
from core.portfolio import Portfolio, TradeRecord
from utils.logger import setup_logger

log = setup_logger("engine")


class TradingEngine:
    def __init__(self):
        self.exchanges: dict[str, BaseExchange] = {}
        self.strategies: list[BaseStrategy] = []
        self.risk_manager = RiskManager()
        self.portfolio = Portfolio()
        self.running = False
        self.scan_interval = 60  # seconds between scans
        self._symbol_exchange_map: dict[str, str] = {}

    def add_exchange(self, name: str, exchange: BaseExchange):
        self.exchanges[name] = exchange
        log.info(f"Added exchange: {name}")

    def add_strategy(self, strategy: BaseStrategy):
        self.strategies.append(strategy)
        log.info(f"Added strategy: {strategy.name}")

    def connect_all(self) -> dict[str, bool]:
        results = {}
        for name, exchange in self.exchanges.items():
            ok = exchange.connect()
            results[name] = ok
            if ok:
                log.info(f"{name} connected")
            else:
                log.error(f"{name} failed to connect")
        return results

    def _get_exchange_for_symbol(self, symbol: str) -> Optional[tuple[str, BaseExchange]]:
        """Determine which exchange handles a given symbol."""
        if symbol in self._symbol_exchange_map:
            name = self._symbol_exchange_map[symbol]
            return name, self.exchanges[name]

        # Auto-detect by market type
        for name, exchange in self.exchanges.items():
            if exchange.market_type == MarketType.CRYPTO and "/" in symbol:
                self._symbol_exchange_map[symbol] = name
                return name, exchange
            if exchange.market_type == MarketType.STOCK and "/" not in symbol and "_" not in symbol:
                self._symbol_exchange_map[symbol] = name
                return name, exchange
            if exchange.market_type == MarketType.FOREX and "_" in symbol:
                self._symbol_exchange_map[symbol] = name
                return name, exchange
        return None

    def scan_symbol(self, symbol: str):
        """Run all strategies against a single symbol."""
        result = self._get_exchange_for_symbol(symbol)
        if not result:
            log.debug(f"No exchange for {symbol}")
            return

        ex_name, exchange = result

        try:
            candles = exchange.get_ohlcv(symbol, "1h", 100)
            ticker = exchange.get_ticker(symbol)
            current_price = ticker.last
        except Exception as e:
            log.error(f"Data fetch failed for {symbol}: {e}")
            return

        # Check stop-loss / take-profit on existing positions
        if self.risk_manager.check_stop_loss(symbol, current_price):
            self._close_position(symbol, exchange, current_price, "stop_loss")
            return
        if self.risk_manager.check_take_profit(symbol, current_price):
            self._close_position(symbol, exchange, current_price, "take_profit")
            return

        # Run each strategy — best signal wins
        best_signal = None
        for strategy in self.strategies:
            signal = strategy.analyze(symbol, candles, current_price)
            if signal.signal != Signal.HOLD:
                if best_signal is None or signal.confidence > best_signal.confidence:
                    best_signal = signal

        if not best_signal:
            return

        # Risk check
        balance = exchange.get_balance()
        portfolio_value = self._estimate_portfolio_value(balance)
        approved, size = self.risk_manager.evaluate_signal(best_signal, portfolio_value)

        if not approved:
            return

        # Execute trade
        self._execute_signal(symbol, exchange, ex_name, best_signal, size, current_price)

    def _execute_signal(self, symbol: str, exchange: BaseExchange,
                        ex_name: str, signal, size: float,
                        current_price: float):
        """Execute a trade based on a signal."""
        quantity = size / current_price if current_price > 0 else 0
        if quantity <= 0:
            return

        side = OrderSide.BUY if signal.signal == Signal.BUY else OrderSide.SELL

        # For SELL signals, close existing position instead of shorting (for spot)
        if signal.signal == Signal.SELL:
            self._close_position(symbol, exchange, current_price, signal.strategy)
            return

        try:
            order = exchange.place_order(
                symbol, side, OrderType.MARKET, quantity
            )
            log.info(
                f"EXECUTED: {side.value} {quantity:.6f} {symbol} @ ${current_price:.4f} "
                f"[{signal.strategy}] confidence={signal.confidence:.2f}"
            )

            self.risk_manager.register_trade(
                symbol, side.value, quantity, current_price
            )
            self.portfolio.record_trade(TradeRecord(
                symbol=symbol,
                side=side.value,
                quantity=quantity,
                entry_price=current_price,
                strategy=signal.strategy,
                market=exchange.market_type.value,
            ))

        except Exception as e:
            log.error(f"Order failed for {symbol}: {e}")

    def _close_position(self, symbol: str, exchange: BaseExchange,
                        current_price: float, reason: str):
        """Close an existing position."""
        try:
            # Get position details from risk manager
            self.risk_manager.close_position(symbol, current_price)
            self.portfolio.close_trade(symbol, current_price)
            log.info(f"Position closed: {symbol} @ ${current_price:.4f} — reason: {reason}")
        except Exception as e:
            log.error(f"Close position failed for {symbol}: {e}")

    def _estimate_portfolio_value(self, balance: dict) -> float:
        """Estimate total portfolio value from balance dict."""
        if "equity" in balance:
            return float(balance["equity"])
        if "portfolio_value" in balance:
            return float(balance["portfolio_value"])
        if "balance" in balance:
            return float(balance["balance"])
        if "nav" in balance:
            return float(balance["nav"])
        # Sum USDT/USD balances for crypto
        total = 0
        for asset, val in balance.items():
            if isinstance(val, dict) and asset in ("USDT", "USD", "BUSD", "USDC"):
                total += val.get("total", 0)
        return total or 10000  # fallback

    def get_symbols_to_scan(self) -> list[str]:
        """Get all symbols to scan based on connected exchanges."""
        from config.settings import settings
        symbols = []
        for name, exchange in self.exchanges.items():
            if exchange.market_type == MarketType.CRYPTO:
                symbols.extend(settings.CRYPTO_PAIRS)
            elif exchange.market_type == MarketType.STOCK:
                symbols.extend(settings.STOCK_SYMBOLS)
            elif exchange.market_type == MarketType.FOREX:
                symbols.extend(settings.FOREX_PAIRS)
        return list(set(symbols))

    def run_scan_cycle(self):
        """Run one full scan across all symbols."""
        symbols = self.get_symbols_to_scan()
        log.info(f"Scanning {len(symbols)} symbols...")

        for symbol in symbols:
            try:
                self.scan_symbol(symbol)
            except Exception as e:
                log.error(f"Scan error for {symbol}: {e}")

        status = self.risk_manager.get_status()
        log.info(f"Cycle complete — {status}")

    def start(self, interval: int = 60):
        """Start the trading loop."""
        self.running = True
        self.scan_interval = interval
        log.info(f"Engine started — scanning every {interval}s")

        while self.running:
            try:
                self.run_scan_cycle()
                time.sleep(self.scan_interval)
            except KeyboardInterrupt:
                log.info("Shutting down...")
                self.stop()
            except Exception as e:
                log.error(f"Engine error: {e}")
                time.sleep(5)

    def stop(self):
        self.running = False
        log.info("Engine stopped")
        perf = self.portfolio.get_performance()
        log.info(f"Final performance: {perf}")

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "exchanges": {n: e.market_type.value for n, e in self.exchanges.items()},
            "strategies": [s.name for s in self.strategies],
            "risk": self.risk_manager.get_status(),
            "performance": self.portfolio.get_performance(),
        }
