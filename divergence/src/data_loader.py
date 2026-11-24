"""
Data loading module for divergence system.
Connects to fetchfu_db SQLite database strictly in read-only mode (?mode=ro).
"""

from __future__ import annotations

import logging
from pathlib import Path
import sqlite3
from typing import List, Optional
import pandas as pd

from src.config import StrategyConfig

logger = logging.getLogger("divergence.data")


class DataLoader:
    """
    Read-only data loader for daily and hourly futures bars from fetchfu_db SQLite database.
    """

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.db_path = config.resolved_db_path
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found at {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def get_available_symbols(self) -> List[str]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol").fetchall()
            return [r[0] for r in rows]

    def get_daily_bars(
        self,
        symbol: str,
        limit_days: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch daily bars for symbol ordered chronologically ascending.
        """
        sym = symbol.upper().strip()
        days = limit_days if limit_days is not None else self.config.history_days_daily
        query = """
            SELECT symbol, datetime, open, high, low, close, volume, open_interest, turnover
            FROM daily_bars
            WHERE symbol = ?
            ORDER BY datetime DESC
            LIMIT ?
        """
        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=(sym, days))

        if df.empty:
            return df

        # Reverse to chronological ascending
        df = df.iloc[::-1].reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume", "open_interest", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def get_hourly_bars(
        self,
        symbol: str,
        limit_days: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch hourly bars for symbol ordered chronologically ascending.
        """
        sym = symbol.upper().strip()
        days = limit_days if limit_days is not None else self.config.history_days_hourly
        limit_rows = days * 10  # ~8-10 hourly bars per trading day
        query = """
            SELECT symbol, datetime, open, high, low, close, volume, open_interest, turnover
            FROM hourly_bars
            WHERE symbol = ?
            ORDER BY datetime DESC
            LIMIT ?
        """
        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=(sym, limit_rows))

        if df.empty:
            return df

        df = df.iloc[::-1].reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume", "open_interest", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
