"""The grid strategy must not load by default.

Measured over 180 days of real hourly bars across 20 symbols: grid generated 96%
of all trade volume and lost $2,468 gross — before a cent of fees. Its payoff is
structurally inverted (18:1 adverse ratio: it takes small profits and holds large
losses), needing roughly a 95% win rate to break even against an actual 44%.
Removing it from the backtest recovered $4,019.

It stays importable and selectable by name for experiments. What must never
happen again is `--strategies all` quietly turning it on, which was the default.
"""

import pytest

from exchanges.base import BaseExchange, MarketType, Ticker


class FakeStockExchange(BaseExchange):
    def __init__(self):
        self.market_type = MarketType.STOCK

    def connect(self): return True
    def get_ticker(self, symbol): return Ticker(symbol, 0, 0, 0, 0, None)
    def get_ohlcv(self, symbol, timeframe="1h", limit=100): return []
    def get_balance(self): return {"equity": 100_000.0}
    def place_order(self, symbol, side, order_type, quantity, price=None): ...
    def get_order_status(self, order_id, symbol): ...
    def cancel_order(self, order_id, symbol): return True
    def get_positions(self): return []


@pytest.fixture
def stock_only(monkeypatch):
    import main
    monkeypatch.setattr(main.settings, "ALPACA_API_KEY", "test-key")
    monkeypatch.setattr(main, "AlpacaExchange", lambda: FakeStockExchange())
    return main


def strategy_names(engine):
    return [s.name.lower() for s in engine.strategies]


def test_all_does_not_include_grid(stock_only):
    engine = stock_only.build_engine(["stocks"], ["all"])
    assert not any("grid" in n for n in strategy_names(engine))


def test_all_still_includes_the_strategies_worth_keeping(stock_only):
    names = strategy_names(stock_only.build_engine(["stocks"], ["all"]))
    assert any("sma" in n for n in names)
    assert any("rsi" in n for n in names)


def test_grid_still_loads_when_asked_for_by_name(stock_only):
    engine = stock_only.build_engine(["stocks"], ["grid"])
    assert any("grid" in n for n in strategy_names(engine))


def test_naming_grid_does_not_drag_in_everything_else(stock_only):
    names = strategy_names(stock_only.build_engine(["stocks"], ["grid"]))
    assert len(names) == 1
