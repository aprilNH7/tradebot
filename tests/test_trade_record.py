"""Unit tests for the Portfolio trade record dataclass."""

from datetime import datetime

from core.portfolio import TradeRecord


def test_trade_record_defaults():
    record = TradeRecord(
        symbol="BTC/USDT",
        side="buy",
        quantity=1.5,
        entry_price=50000.0,
        strategy="sma_crossover",
        market="crypto",
    )
    assert record.exit_price is None
    assert record.pnl == 0.0
    assert record.exit_time is None
    assert isinstance(record.entry_time, datetime)


def test_trade_record_closed_fields():
    record = TradeRecord(
        symbol="ETH/USDT",
        side="sell",
        quantity=2.0,
        entry_price=3000.0,
        exit_price=2900.0,
        pnl=200.0,
        exit_time=datetime.now(),
    )
    assert record.exit_price == 2900.0
    assert record.pnl == 200.0
    assert record.exit_time is not None
