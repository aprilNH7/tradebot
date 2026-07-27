"""Trading Engine — Main orchestrator that ties everything together."""

import time
from datetime import datetime
from typing import Optional

from exchanges.base import BaseExchange, OrderSide, OrderType, MarketType
from strategies.base import BaseStrategy, Signal
from core.risk_manager import RiskManager
from core.portfolio import Portfolio, TradeRecord
from config.settings import settings
from utils.logger import setup_logger

log = setup_logger("engine")


class TradingEngine:
    def __init__(self):
        self.exchanges: dict[str, BaseExchange] = {}
        self.strategies: list[BaseStrategy] = []
        self.risk_manager = RiskManager()
        self.portfolio = Portfolio()
        self.running = False
        self.scan_interval = settings.SCAN_INTERVAL
        self.custom_symbols: list[str] = []
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

        if any(results.values()):
            self._init_capital()
            self._sync_positions()
        return results

    def _sync_positions(self):
        """Adopt positions that already exist on the exchange.

        A restart otherwise leaves live positions untracked: no stop-loss, no
        take-profit, and the duplicate-position guard would happily double up
        on something the account already holds.
        """
        adopted = 0
        for name, exchange in self.exchanges.items():
            try:
                positions = exchange.get_positions()
            except Exception as e:
                log.error(f"Position sync failed for {name}: {e}")
                continue

            for p in positions:
                if self.risk_manager.has_position(p.symbol):
                    continue
                self.risk_manager.register_trade(
                    p.symbol, p.side.value, p.quantity, p.entry_price,
                    count_toward_limits=False,
                )
                self.portfolio.record_trade(TradeRecord(
                    symbol=p.symbol,
                    side=p.side.value,
                    quantity=p.quantity,
                    entry_price=p.entry_price,
                    strategy="adopted",
                    market=exchange.market_type.value,
                ))
                adopted += 1

        if adopted:
            log.info(f"Adopted {adopted} existing position(s) — now risk-managed")

    def _init_capital(self):
        """Seed the portfolio and risk manager with real starting equity.

        Until this runs, peak_balance is 0 — which makes drawdown_pct return 0
        and silently disables the max-drawdown circuit breaker. It also leaves
        ROI undefined in the performance report.
        """
        value = self._fetch_portfolio_value()
        if value <= 0:
            log.warning("Could not determine starting equity; risk limits degraded")
            return
        self.portfolio.set_initial_capital(value)
        self.risk_manager.update_balance(value)
        log.info(f"Starting equity: ${value:,.2f}")

    def _fetch_portfolio_value(self) -> float:
        """Total equity across all connected exchanges."""
        total = 0.0
        for name, exchange in self.exchanges.items():
            try:
                total += self._estimate_portfolio_value(exchange.get_balance())
            except Exception as e:
                log.error(f"Balance fetch failed for {name}: {e}")
        return total

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

    def scan_symbol(self, symbol: str, portfolio_value: Optional[float] = None):
        """Run all strategies against a single symbol."""
        result = self._get_exchange_for_symbol(symbol)
        if not result:
            log.debug(f"No exchange for {symbol}")
            return

        ex_name, exchange = result

        try:
            candles = exchange.get_ohlcv(symbol, settings.CANDLE_TIMEFRAME, 100)
            ticker = exchange.get_ticker(symbol)
            current_price = ticker.last
        except Exception as e:
            log.error(f"Data fetch failed for {symbol}: {e}")
            return

        if current_price <= 0:
            log.warning(f"{symbol}: bad price {current_price}, skipping")
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

        log.info(
            f"SIGNAL {best_signal.signal.value} {symbol} @ ${current_price:.4f} "
            f"[{best_signal.strategy}] confidence={best_signal.confidence:.2f} "
            f"— {best_signal.reason}"
        )

        # An exit on an open position doesn't need sizing or fresh equity
        if best_signal.signal == Signal.SELL and self.risk_manager.has_position(symbol):
            self._close_position(symbol, exchange, current_price, best_signal.strategy)
            return

        # Risk check
        if portfolio_value is None:
            portfolio_value = self._estimate_portfolio_value(exchange.get_balance())
        approved, size = self.risk_manager.evaluate_signal(best_signal, portfolio_value)

        if not approved:
            return

        # Execute trade
        self._execute_signal(symbol, exchange, ex_name, best_signal, size, current_price)

    def _execute_signal(self, symbol: str, exchange: BaseExchange,
                        ex_name: str, signal, size: float,
                        current_price: float):
        """Execute a trade based on a signal."""
        if current_price <= 0:
            return

        quantity = size / current_price
        if not exchange.supports_fractional():
            quantity = float(int(quantity))
            if quantity < 1:
                log.info(
                    f"{symbol}: skipped — ${size:,.2f} at ${current_price:,.2f} "
                    f"is under 1 share (raise RISK_MAX_POSITION_SIZE or drop this symbol)"
                )
                return
        if quantity <= 0:
            return

        side = OrderSide.BUY if signal.signal == Signal.BUY else OrderSide.SELL

        # Spot accounts can't short — a SELL with no open position is a no-op
        if signal.signal == Signal.SELL:
            self._close_position(symbol, exchange, current_price, signal.strategy)
            return

        try:
            order = exchange.place_order(
                symbol, side, OrderType.MARKET, quantity
            )
            entry_price = self._fill_price(exchange, order, current_price, symbol)
            log.info(
                f"EXECUTED: {side.value} {quantity:.6f} {symbol} @ ${entry_price:.4f} "
                f"[{signal.strategy}] confidence={signal.confidence:.2f}"
            )

            self.risk_manager.register_trade(
                symbol, side.value, quantity, entry_price
            )
            self.portfolio.record_trade(TradeRecord(
                symbol=symbol,
                side=side.value,
                quantity=quantity,
                entry_price=entry_price,
                strategy=signal.strategy,
                market=exchange.market_type.value,
            ))

        except Exception as e:
            log.error(f"Order failed for {symbol}: {e}")

    def _fill_price(self, exchange: BaseExchange, order, quoted_price: float,
                    symbol: str) -> float:
        """Actual average fill price, falling back to the pre-trade quote.

        Market orders cross the spread. Booking the quote we saw before sending
        the order credits that spread as profit on both legs of a round trip,
        which inflates reported PnL and can make a losing strategy look like a
        winner. Always prefer what the exchange says we actually paid.
        """
        try:
            filled = exchange.wait_for_fill(order)
        except Exception as e:
            log.warning(f"{symbol}: fill lookup failed ({e}), booking quoted price")
            return quoted_price

        if filled.filled_price:
            slip = filled.filled_price - quoted_price
            if abs(slip) > 1e-9:
                log.debug(
                    f"{symbol}: filled ${filled.filled_price:.4f} vs quote "
                    f"${quoted_price:.4f} (slippage ${slip:+.4f})"
                )
            return filled.filled_price

        log.warning(
            f"{symbol}: no fill price after wait (status={filled.status}), "
            f"booking quoted ${quoted_price:.4f} — reported PnL may drift"
        )
        return quoted_price

    def _close_position(self, symbol: str, exchange: BaseExchange,
                        current_price: float, reason: str):
        """Close an existing position — submits the offsetting order first.

        The books are only updated after the exchange accepts the exit, so a
        rejected order can't leave the bot believing it is flat while it still
        holds risk.
        """
        pos = self.risk_manager.get_position(symbol)
        if not pos:
            log.debug(f"No tracked position for {symbol}, nothing to close")
            return

        is_long = str(pos["side"]).strip().lower() == "buy"
        exit_side = OrderSide.SELL if is_long else OrderSide.BUY
        quantity = pos["quantity"]

        try:
            order = exchange.place_order(symbol, exit_side, OrderType.MARKET, quantity)
        except Exception as e:
            log.error(f"Exit order failed for {symbol} ({reason}): {e}")
            return

        exit_price = self._fill_price(exchange, order, current_price, symbol)

        try:
            self.risk_manager.close_position(symbol, exit_price)
            self.portfolio.close_trade(symbol, exit_price)
            log.info(
                f"CLOSED: {exit_side.value} {quantity:.6f} {symbol} "
                f"@ ${exit_price:.4f} — reason: {reason}"
            )
        except Exception as e:
            log.error(f"Bookkeeping failed after closing {symbol}: {e}")

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
        total = 0.0
        for asset, val in balance.items():
            if isinstance(val, dict) and asset in ("USDT", "USD", "BUSD", "USDC"):
                total += val.get("total", 0)
        return total

    def get_symbols_to_scan(self) -> list[str]:
        """Get all symbols to scan based on connected exchanges."""
        if self.custom_symbols:
            return self.custom_symbols

        symbols = []
        for name, exchange in self.exchanges.items():
            if exchange.market_type == MarketType.CRYPTO:
                symbols.extend(settings.CRYPTO_PAIRS)
            elif exchange.market_type == MarketType.STOCK:
                symbols.extend(settings.STOCK_SYMBOLS)
            elif exchange.market_type == MarketType.FOREX:
                symbols.extend(settings.FOREX_PAIRS)
        return sorted(set(symbols))

    def run_scan_cycle(self):
        """Run one full scan across all symbols."""
        self.risk_manager.maybe_reset_daily()

        # Cache market-open state per exchange so we don't hit the clock
        # endpoint once per symbol.
        open_cache: dict[str, bool] = {}

        def market_open(name: str, exchange: BaseExchange) -> bool:
            if name not in open_cache:
                open_cache[name] = exchange.is_market_open()
                if not open_cache[name]:
                    log.info(f"{name} market closed — skipping its symbols")
            return open_cache[name]

        symbols = []
        for symbol in self.get_symbols_to_scan():
            result = self._get_exchange_for_symbol(symbol)
            if result and market_open(*result):
                symbols.append(symbol)

        if not symbols:
            log.info("All markets closed — idling")
            return

        # One equity read per cycle instead of one per symbol
        portfolio_value = self._fetch_portfolio_value()
        if portfolio_value > 0:
            self.risk_manager.update_balance(portfolio_value)
            if self.portfolio.initial_capital <= 0:
                self.portfolio.set_initial_capital(portfolio_value)

        log.info(f"Scanning {len(symbols)} symbols... equity=${portfolio_value:,.2f}")

        for symbol in symbols:
            try:
                self.scan_symbol(symbol, portfolio_value)
            except Exception as e:
                log.error(f"Scan error for {symbol}: {e}")

        status = self.risk_manager.get_status()
        log.info(f"Cycle complete — {status}")

    def start(self, interval: int = None):
        """Start the trading loop."""
        self.running = True
        self.scan_interval = interval or settings.SCAN_INTERVAL
        log.info(f"Engine started — scanning every {self.scan_interval}s")

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
            "symbols": self.get_symbols_to_scan(),
            "scan_interval": self.scan_interval,
            "risk": self.risk_manager.get_status(),
            "performance": self.portfolio.get_performance(),
        }
