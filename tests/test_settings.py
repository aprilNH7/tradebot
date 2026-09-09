"""Unit tests for settings validation warnings."""

import pytest

from config.settings import Settings


@pytest.fixture
def no_keys(monkeypatch):
    """Clear all credential keys so validation has something to complain about."""
    for attr in (
        "BINANCE_API_KEY",
        "COINBASE_API_KEY",
        "ALPACA_API_KEY",
        "OANDA_ACCESS_TOKEN",
    ):
        monkeypatch.setattr(Settings, attr, "", raising=False)
    return Settings


def test_validate_warns_about_missing_binance(no_keys):
    warnings = no_keys.validate()
    assert any("Binance" in w for w in warnings)


def test_validate_warns_about_missing_alpaca(no_keys):
    warnings = no_keys.validate()
    assert any("Alpaca" in w for w in warnings)


def test_validate_warns_about_missing_oanda(no_keys):
    warnings = no_keys.validate()
    assert any("OANDA" in w for w in warnings)


def test_validate_is_silent_when_all_keys_present(monkeypatch):
    for attr in ("BINANCE_API_KEY", "ALPACA_API_KEY", "OANDA_ACCESS_TOKEN"):
        monkeypatch.setattr(Settings, attr, "test-key", raising=False)
    assert Settings.validate() == []
