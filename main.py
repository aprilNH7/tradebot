#!/usr/bin/env python3
"""TradeBot — Multi-market, multi-strategy automated trading bot."""

import argparse
import os
import sys
import json

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import settings
from core.engine import TradingEngine
from exchanges.binance_exchange import BinanceExchange
from exchanges.coinbase_exchange import CoinbaseExchange
from exchanges.alpaca_exchange import AlpacaExchange
from exchanges.oanda_exchange import OandaExchange
from strategies.sma_crossover import SMACrossoverStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.grid_strategy import GridStrategy
from strategies.arbitrage_strategy import ArbitrageStrategy
from utils.logger import setup_logger

log = setup_logger("main", settings.LOG_LEVEL)


def build_engine(markets: list[str], strategies: list[str],
                 symbols: list[str] = None) -> TradingEngine:
    """Build and configure the trading engine."""
    engine = TradingEngine()

    if symbols:
        engine.custom_symbols = [s.strip().upper() for s in symbols]
        log.info(f"Symbol override: {', '.join(engine.custom_symbols)}")

    # --- Add exchanges ---
    if "crypto" in markets or "all" in markets:
        if settings.BINANCE_API_KEY:
            engine.add_exchange("binance", BinanceExchange())
        if settings.COINBASE_API_KEY:
            engine.add_exchange("coinbase", CoinbaseExchange())

    if "stocks" in markets or "all" in markets:
        if settings.ALPACA_API_KEY:
            engine.add_exchange("alpaca", AlpacaExchange())

    if "forex" in markets or "all" in markets:
        if settings.OANDA_ACCESS_TOKEN:
            engine.add_exchange("oanda", OandaExchange())

    if not engine.exchanges:
        log.error("No exchanges configured! Set API keys in .env file.")
        sys.exit(1)

    # --- Add strategies ---
    crypto_exchanges = [
        ex for ex in engine.exchanges.values()
        if ex.market_type.value == "crypto"
    ]

    if "sma" in strategies or "all" in strategies:
        engine.add_strategy(SMACrossoverStrategy())
    if "rsi" in strategies or "all" in strategies:
        engine.add_strategy(RSIStrategy())
    # Grid is deliberately excluded from "all". Over 180 days of real hourly
    # bars it produced 96% of all trade volume and lost $2,468 gross — before
    # any fees. Its payoff is structurally inverted: it takes small profits and
    # holds large losses, needing roughly a 95% win rate to break even against
    # an actual 44%. Removing it from the 20-symbol backtest recovered $4,019.
    # Still selectable by name for experiments, but never by default.
    if "grid" in strategies:
        log.warning(
            "Grid strategy enabled explicitly — backtested at -$2,468 gross "
            "over 180 days and responsible for 96% of trade volume. "
            "Not recommended with real money."
        )
        engine.add_strategy(GridStrategy())
    if "arbitrage" in strategies or "all" in strategies:
        if len(crypto_exchanges) >= 2:
            engine.add_strategy(ArbitrageStrategy(exchanges=crypto_exchanges))
        else:
            log.warning("Arbitrage needs 2+ crypto exchanges — skipping")

    if not engine.strategies:
        log.error("No strategies selected!")
        sys.exit(1)

    return engine


def main() -> None:
    """CLI entry point — parse arguments, connect exchanges, and start trading."""
    parser = argparse.ArgumentParser(
        description="TradeBot — Multi-market automated trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --markets all --strategies all
  python main.py --markets crypto --strategies sma rsi
  python main.py --markets stocks --strategies rsi --interval 120
  python main.py --markets stocks --symbols AAPL TSLA SPY NVDA
  python main.py --status
  python main.py --dashboard
        """,
    )
    parser.add_argument(
        "--markets", nargs="+",
        choices=["crypto", "stocks", "forex", "all"],
        default=["all"],
        help="Markets to trade (default: all)",
    )
    parser.add_argument(
        "--strategies", nargs="+",
        choices=["sma", "rsi", "grid", "arbitrage", "all"],
        default=["all"],
        help="Strategies to use (default: all)",
    )
    parser.add_argument(
        "--symbols", nargs="+", default=None,
        help="Override the symbol list for this session "
             "(e.g. AAPL TSLA or BTC/USDT ETH/USDT)",
    )
    parser.add_argument(
        "--interval", type=int, default=settings.SCAN_INTERVAL,
        help=f"Scan interval in seconds (default: {settings.SCAN_INTERVAL})",
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Start the web dashboard",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show current configuration and exit",
    )

    args = parser.parse_args()

    # Create logs directory
    os.makedirs("logs", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    # Show config status
    if args.status:
        warnings = settings.validate()
        print("\n=== TradeBot Configuration ===")
        print(f"Markets:    {args.markets}")
        print(f"Strategies: {args.strategies}")
        print(f"Interval:   {args.interval}s")
        print(f"Timeframe:  {settings.CANDLE_TIMEFRAME}")
        print(f"\nExchange Status:")
        print(f"  Binance:  {'OK' if settings.BINANCE_API_KEY else 'NOT CONFIGURED'}")
        print(f"  Coinbase: {'OK' if settings.COINBASE_API_KEY else 'NOT CONFIGURED'}")
        print(f"  Alpaca:   {'OK' if settings.ALPACA_API_KEY else 'NOT CONFIGURED'}")
        print(f"  OANDA:    {'OK' if settings.OANDA_ACCESS_TOKEN else 'NOT CONFIGURED'}")

        print(f"\nWatchlists:")
        if args.symbols:
            print(f"  Override ({len(args.symbols)}): "
                  f"{', '.join(s.upper() for s in args.symbols)}")
        else:
            print(f"  Stocks ({len(settings.STOCK_SYMBOLS)}): "
                  f"{', '.join(settings.STOCK_SYMBOLS)}")
            print(f"  Crypto ({len(settings.CRYPTO_PAIRS)}): "
                  f"{', '.join(settings.CRYPTO_PAIRS)}")
            print(f"  Forex  ({len(settings.FOREX_PAIRS)}): "
                  f"{', '.join(settings.FOREX_PAIRS)}")

        print(f"\nStrategy Parameters:")
        print(f"  SMA:  fast={settings.SMA_FAST_PERIOD} slow={settings.SMA_SLOW_PERIOD}")
        print(f"  RSI:  period={settings.RSI_PERIOD} "
              f"overbought={settings.RSI_OVERBOUGHT} oversold={settings.RSI_OVERSOLD}")
        print(f"  Grid: levels={settings.GRID_LEVELS} "
              f"spacing={settings.GRID_SPACING_PCT:.2%}")
        print(f"  Arb:  min_spread={settings.ARBITRAGE_MIN_SPREAD_PCT:.2%}")

        print(f"\nRisk Limits:")
        print(f"  Max drawdown:     {settings.RISK_MAX_DRAWDOWN:.1%}")
        print(f"  Max position:     {settings.RISK_MAX_POSITION_SIZE:.1%} of portfolio")
        print(f"  Stop loss:        {settings.RISK_STOP_LOSS_PCT:.1%}")
        print(f"  Take profit:      {settings.RISK_TAKE_PROFIT_PCT:.1%}")
        print(f"  Max daily trades: {settings.RISK_MAX_DAILY_TRADES}")
        print(f"  Max open pos:     {settings.RISK_MAX_OPEN_POSITIONS}")
        print(f"  Min confidence:   {settings.RISK_MIN_CONFIDENCE:.2f}")
        if settings.RISK_MAX_DAILY_LOSS > 0:
            print(f"  Daily loss cap:   ${settings.RISK_MAX_DAILY_LOSS:,.2f} "
                  f"(halts new entries for the day)")
        else:
            print(f"  Daily loss cap:   DISABLED")

        print(f"\nExecution:")
        print(f"  Entry orders:     {settings.ENTRY_ORDER_TYPE}"
              + (f" ({settings.LIMIT_PRICE_MODE}, "
                 f"{settings.LIMIT_ENTRY_TIMEOUT:g}s then cancel)"
                 if settings.ENTRY_ORDER_TYPE == "limit" else ""))
        print(f"  Exit orders:      market (guaranteed fill for stops)")

        if warnings:
            print(f"\nWarnings:")
            for w in warnings:
                print(f"  - {w}")
        print()
        return

    print(r"""
  _____              _      ____        _
 |_   _| __ __ _  __| | ___| __ )  ___ | |_
   | || '__/ _` |/ _` |/ _ \  _ \ / _ \| __|
   | || | | (_| | (_| |  __/ |_) | (_) | |_
   |_||_|  \__,_|\__,_|\___|____/ \___/ \__|
    Multi-Market • Multi-Strategy • Automated
    """)

    # Validate config
    warnings = settings.validate()
    for w in warnings:
        log.warning(w)

    # Build engine
    engine = build_engine(args.markets, args.strategies, args.symbols)

    # Connect to exchanges
    log.info("Connecting to exchanges...")
    results = engine.connect_all()

    connected = [name for name, ok in results.items() if ok]
    failed = [name for name, ok in results.items() if not ok]

    if not connected:
        log.error("No exchanges connected! Check your API keys.")
        sys.exit(1)

    log.info(f"Connected: {', '.join(connected)}")
    if failed:
        log.warning(f"Failed: {', '.join(failed)}")

    # Start dashboard in background if requested
    if args.dashboard:
        import threading
        from dashboard.app import create_app
        app = create_app(engine)
        t = threading.Thread(
            target=lambda: app.run(port=settings.DASHBOARD_PORT, debug=False),
            daemon=True,
        )
        t.start()
        log.info(f"Dashboard running at http://localhost:{settings.DASHBOARD_PORT}")

    # Start trading
    log.info(f"Starting trading with {len(engine.strategies)} strategies "
             f"across {len(connected)} exchanges...")
    engine.start(interval=args.interval)


if __name__ == "__main__":
    main()
