import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- Binance ---
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")

    # --- Coinbase ---
    COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
    COINBASE_SECRET_KEY = os.getenv("COINBASE_SECRET_KEY", "")
    COINBASE_PASSPHRASE = os.getenv("COINBASE_PASSPHRASE", "")

    # --- Alpaca ---
    ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
    ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    # --- OANDA ---
    OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
    OANDA_ACCESS_TOKEN = os.getenv("OANDA_ACCESS_TOKEN", "")
    OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice")

    # --- Risk Management ---
    RISK_MAX_DRAWDOWN = float(os.getenv("RISK_MAX_DRAWDOWN", "0.10"))
    RISK_MAX_POSITION_SIZE = float(os.getenv("RISK_MAX_POSITION_SIZE", "0.05"))
    RISK_STOP_LOSS_PCT = float(os.getenv("RISK_STOP_LOSS_PCT", "0.02"))
    RISK_TAKE_PROFIT_PCT = float(os.getenv("RISK_TAKE_PROFIT_PCT", "0.04"))

    # --- General ---
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5050"))
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

    # --- Default Trading Pairs ---
    CRYPTO_PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
    STOCK_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META"]
    FOREX_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD"]

    # --- Strategy Defaults ---
    SMA_FAST_PERIOD = 9
    SMA_SLOW_PERIOD = 21
    RSI_PERIOD = 14
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30
    GRID_LEVELS = 10
    GRID_SPACING_PCT = 0.005

    @classmethod
    def validate(cls):
        warnings = []
        if not cls.BINANCE_API_KEY:
            warnings.append("Binance API keys not set — crypto trading disabled")
        if not cls.ALPACA_API_KEY:
            warnings.append("Alpaca API keys not set — stock trading disabled")
        if not cls.OANDA_ACCESS_TOKEN:
            warnings.append("OANDA credentials not set — forex trading disabled")
        return warnings


settings = Settings()
