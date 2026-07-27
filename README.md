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

# With live dashboard
python main.py --markets all --strategies all --dashboard

# Check configuration
python main.py --status
```

## Dashboard

Start with `--dashboard` flag to access the live monitoring UI at `http://localhost:5050`.

Features:
- Real-time P&L tracking
- Risk management metrics
- Strategy performance breakdown
- Recent trade history

## Risk Management

Built-in risk controls:

- **Max drawdown**: Auto-stops trading at 10% drawdown (configurable)
- **Position sizing**: Kelly-criterion inspired, scaled by signal confidence
- **Stop-loss**: 2% default per position
- **Take-profit**: 4% default per position
- **Daily trade limit**: 50 trades/day
- **Max positions**: 10 concurrent positions

All risk parameters are configurable via `.env`.

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

| Variable | Description | Required |
|----------|-------------|----------|
| `BINANCE_API_KEY` | Binance API key | For crypto |
| `BINANCE_SECRET_KEY` | Binance secret | For crypto |
| `COINBASE_API_KEY` | Coinbase API key | For crypto |
| `COINBASE_SECRET_KEY` | Coinbase secret | For crypto |
| `ALPACA_API_KEY` | Alpaca API key | For stocks |
| `ALPACA_SECRET_KEY` | Alpaca secret | For stocks |
| `ALPACA_BASE_URL` | Alpaca API URL | For stocks |
| `OANDA_ACCOUNT_ID` | OANDA account ID | For forex |
| `OANDA_ACCESS_TOKEN` | OANDA access token | For forex |
| `RISK_MAX_DRAWDOWN` | Max drawdown before halt (default: 0.10) | No |
| `RISK_MAX_POSITION_SIZE` | Max position as % of portfolio (default: 0.05) | No |

## Disclaimer

This software is for educational purposes. Trading involves substantial risk of loss. Use at your own risk. Always start with paper trading to validate strategies before using real funds.

## License

MIT
