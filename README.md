# TradeBot

Multi-market, multi-strategy automated trading bot supporting crypto, stocks, and forex.

```
  _____              _      ____        _
 |_   _| __ __ _  __| | ___| __ )  ___ | |_
   | || '__/ _` |/ _` |/ _ \  _ \ / _ \| __|
   | || | | (_| | (_| |  __/ |_) | (_) | |_
   |_||_|  \__,_|\__,_|\___|____/ \___/ \__|
```

## Supported Markets

| Market | Exchange | API Library |
|--------|----------|-------------|
| Crypto | Binance | ccxt |
| Crypto | Coinbase | ccxt |
| Stocks | Alpaca | alpaca-trade-api |
| Forex | OANDA | oandapyV20 |

## Trading Strategies

| Strategy | Description | Best For |
|----------|-------------|----------|
| **SMA Crossover** | Fast/slow moving average crossover signals | Trending markets |
| **RSI** | Overbought/oversold relative strength index | Mean reversion |
| **Grid Trading** | Buy/sell at fixed price intervals | Ranging markets |
| **Arbitrage** | Exploit price differences across exchanges | Cross-exchange crypto |

## Quick Start

### 1. Install

```bash
git clone https://github.com/aprilNH7/tradebot.git
cd tradebot
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run

```bash
# All markets, all strategies
python main.py --markets all --strategies all

# Crypto only with SMA + RSI
python main.py --markets crypto --strategies sma rsi

# Stocks with RSI, 2-minute scan interval
python main.py --markets stocks --strategies rsi --interval 120

# Trade only a specific watchlist for this session
python main.py --markets stocks --symbols AAPL TSLA SPY NVDA

# With live dashboard
python main.py --markets all --strategies all --dashboard

# Check configuration (watchlists, strategy params, risk limits)
python main.py --status
```

## Customizing Symbols

There are two ways to change what the bot trades.

**Permanent — edit `.env`** (comma-separated, applies to every run):

```bash
STOCK_SYMBOLS=AAPL,MSFT,NVDA,SPY,QQQ,AMD,COIN
CRYPTO_PAIRS=BTC/USDT,ETH/USDT,SOL/USDT
FOREX_PAIRS=EUR_USD,GBP_USD,USD_JPY
```

**One-off — use `--symbols`** (overrides all watchlists for that session):

```bash
python main.py --markets stocks --symbols AAPL TSLA SPY
```

Symbols are routed to the right exchange automatically by format:
`AAPL` → stocks, `BTC/USDT` → crypto, `EUR_USD` → forex.

Defaults: 20 stocks, 10 crypto pairs, 8 forex pairs. Keep the total reasonable —
each symbol costs 2 API calls per scan cycle, so 20 symbols on a 60s interval is
40 calls/min (well inside Alpaca's 200/min limit).

## Tuning Strategies

All strategy parameters are read from `.env` — no code changes needed:

| Variable | Default | Effect |
|----------|---------|--------|
| `SMA_FAST_PERIOD` | 9 | Lower = more frequent crossovers |
| `SMA_SLOW_PERIOD` | 21 | Higher = stronger trend confirmation |
| `RSI_PERIOD` | 14 | Lookback window for RSI |
| `RSI_OVERBOUGHT` | 70 | Lower = sells earlier |
| `RSI_OVERSOLD` | 30 | Higher = buys earlier |
| `GRID_LEVELS` | 10 | Number of grid price levels |
| `GRID_SPACING_PCT` | 0.005 | Gap between levels (0.5%) |
| `ARBITRAGE_MIN_SPREAD_PCT` | 0.005 | Minimum spread to act on |
| `CANDLE_TIMEFRAME` | 1h | Candle size: `1m`, `5m`, `15m`, `1h`, `1d` |
| `SCAN_INTERVAL` | 60 | Seconds between scan cycles |

**More trades**: lower `RISK_MIN_CONFIDENCE`, shorten `CANDLE_TIMEFRAME` to `15m`,
narrow the SMA periods (e.g. `5`/`13`).

**Fewer, higher-quality trades**: raise `RISK_MIN_CONFIDENCE` to `0.5+`, widen
SMA periods (e.g. `20`/`50`), tighten `RSI_OVERSOLD` to `25`.

## Dashboard

Start with `--dashboard` flag to access the live monitoring UI at `http://localhost:5050`.

Features:
- Real-time P&L tracking
- Risk management metrics
- Strategy performance breakdown
- Recent trade history

## Risk Management

Built-in risk controls, all configurable via `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `RISK_MAX_DRAWDOWN` | 0.10 | Halt all trading at 10% drawdown |
| `RISK_MAX_POSITION_SIZE` | 0.05 | Max 5% of portfolio per position |
| `RISK_STOP_LOSS_PCT` | 0.02 | 2% stop-loss per position |
| `RISK_TAKE_PROFIT_PCT` | 0.04 | 4% take-profit per position |
| `RISK_MAX_DAILY_TRADES` | 50 | Daily trade cap |
| `RISK_MAX_OPEN_POSITIONS` | 10 | Concurrent position cap |
| `RISK_MIN_CONFIDENCE` | 0.30 | Ignore signals below this confidence |

Position sizing is Kelly-criterion inspired and scaled by signal confidence, so a
0.8-confidence signal takes a larger position than a 0.35-confidence one.

## Tests

```bash
pytest
```

The suite guards the accounting logic that determines whether the bot makes or
loses money. `OrderSide.BUY.value` is `"buy"` but `Signal.BUY.value` is `"BUY"`,
and code that compared sides case-sensitively silently treated every long as a
short — which inverted reported PnL, fired stop-losses on gains, and disabled the
duplicate-position guard. Every side comparison is therefore parametrised over
each casing a caller might pass.

Any new code comparing a trade side must go through `RiskManager._norm_side()` or
`RiskManager._is_long()` rather than comparing raw strings.

## Architecture

```
tradebot/
├── main.py                 # CLI entry point
├── config/settings.py      # Configuration & environment vars
├── core/
│   ├── engine.py           # Main trading loop orchestrator
│   ├── portfolio.py        # Position & P&L tracking
│   └── risk_manager.py     # Risk controls & position sizing
├── exchanges/
│   ├── base.py             # Abstract exchange interface
│   ├── binance_exchange.py # Binance connector
│   ├── coinbase_exchange.py# Coinbase connector
│   ├── alpaca_exchange.py  # Alpaca stocks connector
│   └── oanda_exchange.py   # OANDA forex connector
├── strategies/
│   ├── base.py             # Base strategy class
│   ├── sma_crossover.py    # SMA crossover strategy
│   ├── rsi_strategy.py     # RSI strategy
│   ├── grid_strategy.py    # Grid trading strategy
│   └── arbitrage_strategy.py # Cross-exchange arbitrage
├── dashboard/
│   ├── app.py              # Flask dashboard
│   └── templates/          # Dashboard HTML
└── utils/
    ├── logger.py           # Colored logging
    └── helpers.py          # Utility functions
```

## Environment Variables

Credentials (see `.env.example` for the full list):

| Variable | Description | Required |
|----------|-------------|----------|
| `BINANCE_API_KEY` | Binance API key | For crypto |
| `BINANCE_SECRET_KEY` | Binance secret | For crypto |
| `COINBASE_API_KEY` | Coinbase API key | For crypto |
| `COINBASE_SECRET_KEY` | Coinbase secret | For crypto |
| `ALPACA_API_KEY` | Alpaca API key | For stocks |
| `ALPACA_SECRET_KEY` | Alpaca secret | For stocks |
| `ALPACA_BASE_URL` | Alpaca API URL (paper vs live) | For stocks |
| `OANDA_ACCOUNT_ID` | OANDA account ID | For forex |
| `OANDA_ACCESS_TOKEN` | OANDA access token | For forex |

Everything else is optional — see [Customizing Symbols](#customizing-symbols),
[Tuning Strategies](#tuning-strategies), and [Risk Management](#risk-management).
Run `python main.py --status` to print the fully resolved configuration.

## Disclaimer

This software is for educational purposes. Trading involves substantial risk of loss. Use at your own risk. Always start with paper trading to validate strategies before using real funds.

## License

MIT
