"""
High-performance read-only query SDK for futures daily and hourly bars.
Designed for downstream agents, strategies, and backtesting systems.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd

from src.config import AppConfig, load_config


class FuturesReader:
    """
    Read-only client SDK to query domestic futures historical bar data from SQLite.
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path is None:
            config = load_config()
            self.db_path = config.resolved_db_path
        else:
            self.db_path = Path(db_path).resolve()

        if not self.db_path.exists():
            raise FileNotFoundError(f"Futures database not found at {self.db_path}")

    @classmethod
    def from_config(cls, config: AppConfig) -> FuturesReader:
        return cls(db_path=config.resolved_db_path)

    def _get_connection(self) -> sqlite3.Connection:
        """Create a read-only SQLite connection."""
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def get_daily_bars(
        self,
        symbol: Union[str, List[str]],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        as_dataframe: bool = True,
    ) -> Union[pd.DataFrame, List[Dict[str, Any]]]:
        """
        Query daily bars for one or more symbols.

        :param symbol: Single symbol (e.g. 'RB0') or list of symbols (e.g. ['RB0', 'CU0']).
        :param start_date: Start date string (inclusive, YYYY-MM-DD).
        :param end_date: End date string (inclusive, YYYY-MM-DD).
        :param limit: Max number of records to return.
        :param as_dataframe: Return pandas.DataFrame if True, list of dicts if False.
        """
        return self._query_bars(
            table_name="daily_bars",
            symbol=symbol,
            start_dt=start_date,
            end_dt=end_date,
            limit=limit,
            as_dataframe=as_dataframe,
        )

    def get_hourly_bars(
        self,
        symbol: Union[str, List[str]],
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: Optional[int] = None,
        as_dataframe: bool = True,
    ) -> Union[pd.DataFrame, List[Dict[str, Any]]]:
        """
        Query hourly (60m) bars for one or more symbols.

        :param symbol: Single symbol (e.g. 'RB0') or list of symbols.
        :param start_time: Start datetime string (inclusive, e.g. '2026-01-01' or '2026-01-01 09:00:00').
        :param end_time: End datetime string (inclusive, e.g. '2026-11-20 15:00:00').
        :param limit: Max number of records to return.
        :param as_dataframe: Return pandas.DataFrame if True, list of dicts if False.
        """
        return self._query_bars(
            table_name="hourly_bars",
            symbol=symbol,
            start_dt=start_time,
            end_dt=end_time,
            limit=limit,
            as_dataframe=as_dataframe,
        )

    def _query_bars(
        self,
        table_name: str,
        symbol: Union[str, List[str]],
        start_dt: Optional[str] = None,
        end_dt: Optional[str] = None,
        limit: Optional[int] = None,
        as_dataframe: bool = True,
    ) -> Union[pd.DataFrame, List[Dict[str, Any]]]:
        symbols = [s.upper().strip() for s in ([symbol] if isinstance(symbol, str) else symbol)]
        if not symbols:
            return pd.DataFrame() if as_dataframe else []

        placeholders = ",".join("?" for _ in symbols)
        query = f"""
            SELECT symbol, datetime, open, high, low, close, volume, open_interest, turnover, updated_at
            FROM {table_name}
            WHERE symbol IN ({placeholders})
        """
        params: List[Any] = list(symbols)

        if start_dt:
            query += " AND datetime >= ?"
            params.append(start_dt.strip())

        if end_dt:
            # If date only (length 10) for hourly table, extend to end of day
            ed = end_dt.strip()
            if table_name == "hourly_bars" and len(ed) == 10:
                ed = f"{ed} 23:59:59"
            query += " AND datetime <= ?"
            params.append(ed)

        query += " ORDER BY symbol ASC, datetime ASC"

        if limit is not None and limit > 0:
            query += f" LIMIT {int(limit)}"

        conn = self._get_connection()
        try:
            if as_dataframe:
                df = pd.read_sql_query(query, conn, params=params)
                # Numeric type casting
                for col in ["open", "high", "low", "close", "volume", "open_interest", "turnover"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                return df
            else:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_latest_bar(
        self,
        symbol: str,
        period: str = "1d",
    ) -> Optional[Dict[str, Any]]:
        """
        Get the most recent bar for a symbol.
        :param symbol: Symbol code (e.g. 'RB0').
        :param period: '1d' or '60m'.
        """
        table_name = "daily_bars" if period == "1d" else "hourly_bars"
        sym = symbol.upper().strip()
        query = f"""
            SELECT symbol, datetime, open, high, low, close, volume, open_interest, turnover, updated_at
            FROM {table_name}
            WHERE symbol = ?
            ORDER BY datetime DESC
            LIMIT 1
        """
        conn = self._get_connection()
        try:
            row = conn.execute(query, (sym,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_symbols(self, period: str = "1d") -> List[str]:
        """
        Return list of all distinct symbols stored in database for the given period.
        """
        table_name = "daily_bars" if period == "1d" else "hourly_bars"
        conn = self._get_connection()
        try:
            rows = conn.execute(
                f"SELECT DISTINCT symbol FROM {table_name} ORDER BY symbol"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def get_date_range(self, symbol: str, period: str = "1d") -> Tuple[Optional[str], Optional[str]]:
        """
        Return (min_datetime, max_datetime) for a symbol.
        """
        table_name = "daily_bars" if period == "1d" else "hourly_bars"
        sym = symbol.upper().strip()
        conn = self._get_connection()
        try:
            row = conn.execute(
                f"SELECT MIN(datetime), MAX(datetime) FROM {table_name} WHERE symbol = ?",
                (sym,),
            ).fetchone()
            if row:
                return row[0], row[1]
            return None, None
        finally:
            conn.close()
