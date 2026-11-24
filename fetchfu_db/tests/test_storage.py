"""
Unit tests for SQLite storage manager, WAL mode, and idempotent UPSERT.
"""

from pathlib import Path
import tempfile
import pandas as pd
import pytest

from src.storage import StorageManager


@pytest.fixture
def temp_storage():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    storage = StorageManager(db_path=db_path, wal_mode=True)
    yield storage
    # Cleanup
    for ext in ["", "-wal", "-shm"]:
        Path(f"{db_path}{ext}").unlink(missing_ok=True)


def test_db_initialization(temp_storage):
    with temp_storage.get_connection() as conn:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "daily_bars" in tables
        assert "hourly_bars" in tables

        # Verify WAL mode
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"


def test_upsert_daily_bars(temp_storage):
    df = pd.DataFrame([
        {
            "symbol": "RB0",
            "datetime": "2026-11-19",
            "open": 3000.0,
            "high": 3050.0,
            "low": 2980.0,
            "close": 3020.0,
            "volume": 500000.0,
            "open_interest": 1500000.0,
            "turnover": 0.0,
        },
        {
            "symbol": "RB0",
            "datetime": "2026-11-20",
            "open": 3020.0,
            "high": 3080.0,
            "low": 3010.0,
            "close": 3070.0,
            "volume": 600000.0,
            "open_interest": 1600000.0,
            "turnover": 0.0,
        },
    ])

    count = temp_storage.upsert_daily_bars("RB0", df)
    assert count == 2

    # Verify data in database
    with temp_storage.get_connection() as conn:
        rows = conn.execute("SELECT * FROM daily_bars WHERE symbol='RB0' ORDER BY datetime").fetchall()
        assert len(rows) == 2
        assert rows[0]["datetime"] == "2026-11-19"
        assert rows[0]["close"] == 3020.0

    # Test Idempotent UPSERT: Update close price for 2026-11-20
    df_update = pd.DataFrame([
        {
            "symbol": "RB0",
            "datetime": "2026-11-20",
            "open": 3020.0,
            "high": 3090.0,
            "low": 3010.0,
            "close": 3085.0,  # Changed
            "volume": 650000.0,
            "open_interest": 1610000.0,
            "turnover": 100.0,
        }
    ])
    temp_storage.upsert_daily_bars("RB0", df_update)

    with temp_storage.get_connection() as conn:
        rows = conn.execute("SELECT * FROM daily_bars WHERE symbol='RB0' ORDER BY datetime").fetchall()
        assert len(rows) == 2  # No duplicate row inserted
        assert rows[1]["datetime"] == "2026-11-20"
        assert rows[1]["close"] == 3085.0
        assert rows[1]["high"] == 3090.0
        assert rows[1]["volume"] == 650000.0


def test_upsert_hourly_bars(temp_storage):
    df_hourly = pd.DataFrame([
        {
            "symbol": "CU0",
            "datetime": "2026-11-20 10:00:00",
            "open": 74000.0,
            "high": 74500.0,
            "low": 73800.0,
            "close": 74200.0,
            "volume": 12000.0,
            "open_interest": 80000.0,
            "turnover": 0.0,
        },
        {
            "symbol": "CU0",
            "datetime": "2026-11-20 11:00:00",
            "open": 74200.0,
            "high": 74600.0,
            "low": 74100.0,
            "close": 74400.0,
            "volume": 9000.0,
            "open_interest": 80500.0,
            "turnover": 0.0,
        },
    ])

    count = temp_storage.upsert_hourly_bars("CU0", df_hourly)
    assert count == 2

    stats = temp_storage.get_database_status()
    assert stats["hourly_bars"]["total_rows"] == 2
    assert stats["hourly_bars"]["symbols_count"] == 1
    assert "CU0" in stats["hourly_bars"]["symbols"]
