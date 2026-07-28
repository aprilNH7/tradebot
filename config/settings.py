import os
from dotenv import load_dotenv

load_dotenv()


def _csv(key: str, default: list[str]) -> list[str]:
    """Read a comma-separated env var into a list, falling back to default."""
    raw = os.getenv(key, "")
    if not raw.strip():
        return default
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


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
    RISK_MAX_DAILY_TRADES = int(os.getenv("RISK_MAX_DAILY_TRADES", "50"))
    RISK_MAX_OPEN_POSITIONS = int(os.getenv("RISK_MAX_OPEN_POSITIONS", "10"))
    RISK_MIN_CONFIDENCE = float(os.getenv("RISK_MIN_CONFIDENCE", "0.30"))

    # Hard dollar floor for a single day. Measured against the day's opening
    # equity, so it captures unrealised losses too — a position sitting 5% under
    # water counts immediately instead of only when it is finally closed.
    # Once tripped the bot stops opening positions for the rest of the calendar
    # day; it keeps managing exits so existing stops still fire.
    # Set to 0 to disable (not recommended — the max-drawdown check alone lets a
    # single bad session run down 10% of the account before it reacts).
    RISK_MAX_DAILY_LOSS = float(os.getenv("RISK_MAX_DAILY_LOSS", "200"))

    # --- Order Execution ---
    # Entries post passively instead of crossing the spread. Market entries pay
    # the full spread on the way in and again on the way out; over 4,892 round
    # trips in the 180-day backtest that cost was the single largest controllable
    # drag (~$17/day). Set to "market" to go back to crossing.
    ENTRY_ORDER_TYPE = os.getenv("ENTRY_ORDER_TYPE", "limit").strip().lower()
    # "passive" rests at the bid (buys) / ask (sells) and pays no spread.
    # "mid" splits the spread for a better fill rate at half the saving.
    LIMIT_PRICE_MODE = os.getenv("LIMIT_PRICE_MODE", "passive").strip().lower()
    # How long to leave a resting entry before cancelling it. Unfilled entries
    # are abandoned rather than chased — these signals have no edge worth paying
    # up for, and chasing reintroduces the cost this change removes.
    LIMIT_ENTRY_TIMEOUT = float(os.getenv("LIMIT_ENTRY_TIMEOUT", "20"))

    # --- General ---
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5050"))
    SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
    CANDLE_TIMEFRAME = os.getenv("CANDLE_TIMEFRAME", "1h")
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

    # --- Trading Pairs (override via comma-separated env vars) ---
    CRYPTO_PAIRS = _csv("CRYPTO_PAIRS", [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
        "XRP/USDT", "ADA/USDT", "DOGE/USDT", "AVAX/USDT",
        "LINK/USDT", "DOT/USDT",
    ])
    STOCK_SYMBOLS = _csv("STOCK_SYMBOLS", [
        "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META",
        "AMD", "NFLX", "SPY", "QQQ", "JPM", "V", "DIS",
        "COIN", "PLTR", "UBER", "BA", "INTC", "SOFI",
    ])
    FOREX_PAIRS = _csv("FOREX_PAIRS", [
        "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD",
        "USD_CAD", "USD_CHF", "NZD_USD", "EUR_GBP",
    ])

    # --- Strategy Defaults ---
    SMA_FAST_PERIOD = int(os.getenv("SMA_FAST_PERIOD", "9"))
    SMA_SLOW_PERIOD = int(os.getenv("SMA_SLOW_PERIOD", "21"))
    RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
    RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", "70"))
    RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", "30"))
    GRID_LEVELS = int(os.getenv("GRID_LEVELS", "10"))
    GRID_SPACING_PCT = float(os.getenv("GRID_SPACING_PCT", "0.005"))
    ARBITRAGE_MIN_SPREAD_PCT = float(os.getenv("ARBITRAGE_MIN_SPREAD_PCT", "0.005"))

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
