"""
SQLite storage engine with WAL mode and idempotent UPSERT for futures bar data.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple, Union
import pandas as pd

from src.config import AppConfig

logger = logging.getLogger("fetchfu.storage")

# Shanghai Timezone (UTC+8)
CST = timezone(timedelta(hours=8))

DAILY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol          TEXT NOT NULL,
    datetime        TEXT NOT NULL,
    open            REAL NOT NULL,
    high            REAL NOT NULL,
    low             REAL NOT NULL,
    close           REAL NOT NULL,
    volume          REAL DEFAULT 0,
    open_interest   REAL DEFAULT 0,
    turnover        REAL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (symbol, datetime)
);
"""

DAILY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_daily_symbol_dt ON daily_bars (symbol, datetime DESC);
"""

HOURLY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS hourly_bars (
    symbol          TEXT NOT NULL,
    datetime        TEXT NOT NULL,
    open            REAL NOT NULL,
    high            REAL NOT NULL,
    low             REAL NOT NULL,
    close           REAL NOT NULL,
    volume          REAL DEFAULT 0,
    open_interest   REAL DEFAULT 0,
    turnover        REAL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (symbol, datetime)
);
"""

HOURLY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_hourly_symbol_dt ON hourly_bars (symbol, datetime DESC);
"""

UPSERT_DAILY_SQL = """
INSERT INTO daily_bars (
    symbol, datetime, open, high, low, close, volume, open_interest, turnover, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, datetime) DO UPDATE SET
    open = excluded.open,
    high = excluded.high,
    low = excluded.low,
    close = excluded.close,
    volume = excluded.volume,
    open_interest = excluded.open_interest,
    turnover = excluded.turnover,
    updated_at = excluded.updated_at;
"""

UPSERT_HOURLY_SQL = """
INSERT INTO hourly_bars (
    symbol, datetime, open, high, low, close, volume, open_interest, turnover, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, datetime) DO UPDATE SET
    open = excluded.open,
    high = excluded.high,
    low = excluded.low,
    close = excluded.close,
    volume = excluded.volume,
    open_interest = excluded.open_interest,
    turnover = excluded.turnover,
    updated_at = excluded.updated_at;
"""


class StorageManager:
    """
    Manages SQLite database connections, initialization, and bulk UPSERTs.
    """

    def __init__(self, db_path: Union[str, Path], wal_mode: bool = True):
        self.db_path = Path(db_path).resolve()
        self.wal_mode = wal_mode
        self._ensure_dir()
        self.init_db()

    @classmethod
    def from_config(cls, config: AppConfig) -> StorageManager:
        return cls(
            db_path=config.resolved_db_path,
            wal_mode=config.database.wal_mode,
        )

    def _ensure_dir(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Yield a configured sqlite3 connection with WAL mode and pragmas.
        """
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        try:
            if self.wal_mode:
                conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        """
        Initialize tables and indexes if they do not exist.
        """
        with self.get_connection() as conn:
            with conn:
                conn.execute(DAILY_TABLE_DDL)
                conn.execute(DAILY_INDEX_DDL)
                conn.execute(HOURLY_TABLE_DDL)
                conn.execute(HOURLY_INDEX_DDL)
        logger.debug(f"Database schema initialized at {self.db_path}")

    @staticmethod
    def _get_current_cst_time() -> str:
        return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")

    def _prepare_records(
        self,
        symbol: str,
        data: Union[pd.DataFrame, List[Dict[str, Any]], List[Tuple]],
    ) -> List[Tuple]:
        """
        Normalize input into list of tuples matching:
        (symbol, datetime, open, high, low, close, volume, open_interest, turnover, updated_at)
        """
        if data is None or (isinstance(data, pd.DataFrame) and data.empty):
            return []

        now_str = self._get_current_cst_time()
        records: List[Tuple] = []

        if isinstance(data, pd.DataFrame):
            df = data.copy()
            # Ensure required columns
            for col in ["open", "high", "low", "close"]:
                if col not in df.columns:
                    return []
            for col in ["volume", "open_interest", "turnover"]:
                if col not in df.columns:
                    df[col] = 0.0

            for _, row in df.iterrows():
                dt_str = str(row["datetime"]).strip()
                try:
                    records.append((
                        symbol,
                        dt_str,
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row.get("volume", 0.0) or 0.0),
                        float(row.get("open_interest", 0.0) or 0.0),
                        float(row.get("turnover", 0.0) or 0.0),
                        now_str,
                    ))
                except (ValueError, TypeError):
                    continue

        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    try:
                        records.append((
                            item.get("symbol", symbol),
                            str(item["datetime"]).strip(),
                            float(item["open"]),
                            float(item["high"]),
                            float(item["low"]),
                            float(item["close"]),
                            float(item.get("volume", 0.0) or 0.0),
                            float(item.get("open_interest", 0.0) or 0.0),
                            float(item.get("turnover", 0.0) or 0.0),
                            now_str,
                        ))
                    except (KeyError, ValueError, TypeError):
                        continue
                elif isinstance(item, (tuple, list)):
                    # Assume tuple matches (symbol, datetime, open, high, low, close, volume, open_interest, turnover)
                    # or (datetime, open, high, low, close, volume, open_interest, turnover)
                    try:
                        if len(item) == 9:
                            records.append((
                                symbol,
                                str(item[0]).strip(),
                                float(item[1]),
                                float(item[2]),
                                float(item[3]),
                                float(item[4]),
                                float(item[5]),
                                float(item[6]),
                                float(item[7]),
                                str(item[8]) if isinstance(item[8], str) else now_str,
                            ))
                        elif len(item) == 10:
                            records.append(tuple(item))
                    except (ValueError, TypeError, IndexError):
                        continue

        return records

    def upsert_daily_bars(
        self,
        symbol: str,
        data: Union[pd.DataFrame, List[Dict[str, Any]], List[Tuple]],
    ) -> int:
        """
        Bulk upsert daily bars for a symbol. Returns count of rows processed.
        """
        records = self._prepare_records(symbol, data)
        if not records:
            return 0

        with self.get_connection() as conn:
            with conn:
                conn.executemany(UPSERT_DAILY_SQL, records)
        logger.debug(f"Upserted {len(records)} daily bars for {symbol}")
        return len(records)

    def upsert_hourly_bars(
        self,
        symbol: str,
        data: Union[pd.DataFrame, List[Dict[str, Any]], List[Tuple]],
    ) -> int:
        """
        Bulk upsert hourly bars for a symbol. Returns count of rows processed.
        """
        records = self._prepare_records(symbol, data)
        if not records:
            return 0

        with self.get_connection() as conn:
            with conn:
                conn.executemany(UPSERT_HOURLY_SQL, records)
        logger.debug(f"Upserted {len(records)} hourly bars for {symbol}")
        return len(records)

    def get_symbol_latest_datetime(
        self, symbol: str, table_name: str = "daily_bars"
    ) -> Optional[str]:
        """
        Get the maximum datetime recorded for a given symbol in daily_bars or hourly_bars.
        """
        table = "daily_bars" if "daily" in table_name.lower() else "hourly_bars"
        with self.get_connection() as conn:
            row = conn.execute(
                f"SELECT MAX(datetime) FROM {table} WHERE symbol = ?", (symbol,)
            ).fetchone()
            if row and row[0]:
                return str(row[0])
            return None

    def get_database_status(self) -> Dict[str, Any]:
        """
        Query and return summary statistics of the database.
        """
        with self.get_connection() as conn:
            daily_total = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
            hourly_total = conn.execute("SELECT COUNT(*) FROM hourly_bars").fetchone()[0]

            daily_symbols = [
                row[0] for row in conn.execute(
                    "SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol"
                ).fetchall()
            ]
            hourly_symbols = [
                row[0] for row in conn.execute(
                    "SELECT DISTINCT symbol FROM hourly_bars ORDER BY symbol"
                ).fetchall()
            ]

            daily_range = conn.execute(
                "SELECT MIN(datetime), MAX(datetime) FROM daily_bars"
            ).fetchone()
            hourly_range = conn.execute(
                "SELECT MIN(datetime), MAX(datetime) FROM hourly_bars"
            ).fetchone()

        file_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        file_size_mb = round(file_size_bytes / (1024 * 1024), 2)

        return {
            "db_path": str(self.db_path),
            "file_size_mb": file_size_mb,
            "daily_bars": {
                "total_rows": daily_total,
                "symbols_count": len(daily_symbols),
                "symbols": daily_symbols,
                "min_date": daily_range[0] if daily_range else None,
                "max_date": daily_range[1] if daily_range else None,
            },
            "hourly_bars": {
                "total_rows": hourly_total,
                "symbols_count": len(hourly_symbols),
                "symbols": hourly_symbols,
                "min_datetime": hourly_range[0] if hourly_range else None,
                "max_datetime": hourly_range[1] if hourly_range else None,
            },
        }
