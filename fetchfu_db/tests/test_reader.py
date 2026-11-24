"""
Unit tests for the downstream FuturesReader SDK.
"""

from pathlib import Path
import tempfile
import pandas as pd
import pytest

from src.reader import FuturesReader
from src.storage import StorageManager


@pytest.fixture
def populated_reader():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    storage = StorageManager(db_path=db_path)

    # Insert daily bars
    daily_data = pd.DataFrame([
        {"datetime": "2026-11-01", "open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 1000.0, "open_interest": 5000.0, "turnover": 0.0},
        {"datetime": "2026-11-02", "open": 102.0, "high": 108.0, "low": 101.0, "close": 106.0, "volume": 1200.0, "open_interest": 5200.0, "turnover": 0.0},
        {"datetime": "2026-11-03", "open": 106.0, "high": 107.0, "low": 103.0, "close": 104.0, "volume": 800.0, "open_interest": 5100.0, "turnover": 0.0},
    ])
    storage.upsert_daily_bars("RB0", daily_data)

    # Insert hourly bars
    hourly_data = pd.DataFrame([
        {"datetime": "2026-11-03 09:00:00", "open": 104.0, "high": 105.0, "low": 103.5, "close": 104.5, "volume": 200.0, "open_interest": 5150.0, "turnover": 0.0},
        {"datetime": "2026-11-03 10:00:00", "open": 104.5, "high": 106.0, "low": 104.0, "close": 105.0, "volume": 300.0, "open_interest": 5200.0, "turnover": 0.0},
    ])
    storage.upsert_hourly_bars("RB0", hourly_data)

    reader = FuturesReader(db_path=db_path)
    yield reader

    for ext in ["", "-wal", "-shm"]:
        Path(f"{db_path}{ext}").unlink(missing_ok=True)


def test_reader_get_daily_bars(populated_reader):
    df = populated_reader.get_daily_bars("RB0")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    assert list(df["datetime"]) == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert df["close"].iloc[-1] == 104.0


def test_reader_date_filtering(populated_reader):
    df = populated_reader.get_daily_bars(
        "RB0",
        start_date="2026-11-02",
        end_date="2026-11-02",
    )
    assert len(df) == 1
    assert df["datetime"].iloc[0] == "2026-11-02"


def test_reader_get_hourly_bars(populated_reader):
    df = populated_reader.get_hourly_bars("RB0")
    assert len(df) == 2
    assert "2026-11-03 09:00:00" in df["datetime"].values


def test_reader_get_latest_bar(populated_reader):
    bar = populated_reader.get_latest_bar("RB0", period="1d")
    assert bar is not None
    assert bar["datetime"] == "2026-11-03"
    assert bar["close"] == 104.0


def test_reader_get_symbols(populated_reader):
    symbols = populated_reader.get_symbols("1d")
    assert symbols == ["RB0"]


def test_reader_get_date_range(populated_reader):
    min_dt, max_dt = populated_reader.get_date_range("RB0", "1d")
    assert min_dt == "2026-11-01"
    assert max_dt == "2026-11-03"
