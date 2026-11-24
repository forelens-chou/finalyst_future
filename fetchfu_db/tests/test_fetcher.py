"""
Unit tests for data fetching, sanitization, and filtering.
"""

from unittest.mock import patch
import pandas as pd
import pytest

from src.config import load_config
from src.fetcher import FuturesFetcher


def test_daily_data_sanitization():
    mock_raw_df = pd.DataFrame([
        {
            "date": "2026-11-19",
            "open": "3000.5",
            "high": "3050.0",
            "low": "2980.0",
            "close": "3020.0",
            "volume": 50000,
            "hold": 150000,
            "settle": 3010.0,
        },
        {
            # Row with NaN close - should be dropped
            "date": "2026-11-20",
            "open": 3020.0,
            "high": 3080.0,
            "low": 3010.0,
            "close": None,
            "volume": 60000,
            "hold": 160000,
            "settle": 3050.0,
        },
    ])

    fetcher = FuturesFetcher()
    with patch("akshare.futures_zh_daily_sina", return_value=mock_raw_df):
        cleaned = fetcher.fetch_daily_bars("RB0", history_days=30)

    assert len(cleaned) == 1
    row = cleaned.iloc[0]
    assert row["symbol"] == "RB0"
    assert row["datetime"] == "2026-11-19"
    assert row["open"] == 3000.5
    assert row["close"] == 3020.0
    assert row["open_interest"] == 150000.0
    assert row["turnover"] == 0.0


def test_hourly_data_sanitization():
    mock_min_df = pd.DataFrame([
        {
            "datetime": "2026-11-20 10:00:00",
            "open": 74000.0,
            "high": 74500.0,
            "low": 73800.0,
            "close": 74200.0,
            "volume": 12000,
            "hold": 80000,
        }
    ])

    fetcher = FuturesFetcher()
    with patch("akshare.futures_zh_min_sina", return_value=mock_min_df):
        cleaned = fetcher.fetch_hourly_bars("CU0", history_days=30)

    assert len(cleaned) == 1
    row = cleaned.iloc[0]
    assert row["symbol"] == "CU0"
    assert row["datetime"] == "2026-11-20 10:00:00"
    assert row["open"] == 74000.0
    assert row["open_interest"] == 80000.0
