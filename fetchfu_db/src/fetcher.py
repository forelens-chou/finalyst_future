"""
AkShare futures data fetcher and sanitizer for daily and hourly bars.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional
import akshare as ak
import numpy as np
import pandas as pd

from src.config import AppConfig
from src.network import retry_with_backoff

logger = logging.getLogger("fetchfu.fetcher")

# China Standard Time (UTC+8)
CST = timezone(timedelta(hours=8))


class FuturesFetcher:
    """
    Fetches raw futures market data via AkShare and sanitizes into canonical schema.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config
        self.history_days = config.fetch.history_days if config else 365
        self.hourly_period = config.fetch.hourly_period if config else "60"

    @staticmethod
    def _get_cutoff_date(days: int) -> str:
        """Return cutoff date string YYYY-MM-DD in CST."""
        now = datetime.now(CST)
        cutoff = now - timedelta(days=days)
        return cutoff.strftime("%Y-%m-%d")

    @staticmethod
    def _get_cutoff_datetime(days: int) -> str:
        """Return cutoff datetime string YYYY-MM-DD 00:00:00 in CST."""
        now = datetime.now(CST)
        cutoff = now - timedelta(days=days)
        return cutoff.strftime("%Y-%m-%d 00:00:00")

    def _sanitize_daily_df(
        self,
        raw_df: pd.DataFrame,
        symbol: str,
        history_days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Sanitize raw daily DataFrame into canonical schema."""
        sym = symbol.upper().strip()
        if raw_df is None or raw_df.empty:
            return pd.DataFrame(columns=[
                "symbol", "datetime", "open", "high", "low", "close", "volume", "open_interest", "turnover"
            ])

        df = raw_df.copy()
        rename_map = {
            "date": "datetime",
            "hold": "open_interest",
            "日期": "datetime",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "持仓量": "open_interest",
            "成交额": "turnover",
        }
        df = df.rename(columns=rename_map)

        # Standardize datetime column to YYYY-MM-DD
        df["datetime"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d")

        for col in ["turnover", "open_interest", "volume"]:
            if col not in df.columns:
                df[col] = 0.0

        for col in ["open", "high", "low", "close", "volume", "open_interest", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
        df["volume"] = df["volume"].fillna(0.0)
        df["open_interest"] = df["open_interest"].fillna(0.0)
        df["turnover"] = df["turnover"].fillna(0.0)

        days = history_days if history_days is not None else self.history_days
        if days and days > 0:
            cutoff = self._get_cutoff_date(days)
            df = df[df["datetime"] >= cutoff]

        df["symbol"] = sym
        cols = ["symbol", "datetime", "open", "high", "low", "close", "volume", "open_interest", "turnover"]
        return df[cols].reset_index(drop=True)

    def _fetch_daily_bars_em_fallback(
        self,
        symbol: str,
        history_days: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """Fallback to Eastmoney (futures_hist_em) when primary Sina source fails."""
        try:
            from src.symbols import get_symbol_metadata
            meta_map = get_symbol_metadata()
            target_meta = meta_map.get(symbol.upper(), {})
            raw_name = target_meta.get("name", symbol)
            target_name = raw_name.replace("连续", "主连")
            logger.info(f"[{symbol}] Attempting fallback source EastMoney with target '{target_name}'...")
            raw_df = ak.futures_hist_em(symbol=target_name, period="daily")
            if raw_df is not None and not raw_df.empty:
                logger.info(f"[{symbol}] Fallback EastMoney daily fetch succeeded ({len(raw_df)} bars).")
                return self._sanitize_daily_df(raw_df, symbol, history_days)
        except Exception as e:
            logger.warning(f"[{symbol}] Fallback EastMoney daily fetch failed: {e}")
        return None

    @retry_with_backoff(retries=3, backoff_factor=1.5, initial_delay=1.0)
    def fetch_daily_bars(
        self,
        symbol: str,
        history_days: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch daily bar history for a given symbol from Sina Futures with EastMoney fallback.

        Returns DataFrame with standardized columns:
        ['symbol', 'datetime', 'open', 'high', 'low', 'close', 'volume', 'open_interest', 'turnover']
        """
        sym = symbol.upper().strip()
        logger.debug(f"Fetching daily bars for {sym}")

        try:
            raw_df = ak.futures_zh_daily_sina(symbol=sym)
            return self._sanitize_daily_df(raw_df, sym, history_days)
        except Exception as e:
            logger.warning(f"Primary source Sina failed for {sym}: {e}. Trying EastMoney fallback...")
            fallback_df = self._fetch_daily_bars_em_fallback(sym, history_days)
            if fallback_df is not None and not fallback_df.empty:
                return fallback_df
            raise

    @retry_with_backoff(retries=3, backoff_factor=1.5, initial_delay=1.0)
    def fetch_hourly_bars(
        self,
        symbol: str,
        history_days: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch hourly (60m) bar history for a given symbol from Sina Futures.

        Returns DataFrame with standardized columns:
        ['symbol', 'datetime', 'open', 'high', 'low', 'close', 'volume', 'open_interest', 'turnover']
        """
        sym = symbol.upper().strip()
        period = self.hourly_period
        logger.debug(f"Fetching hourly ({period}m) bars for {sym}")

        # Support both ak.futures_zh_min_sina and ak.futures_zh_minute_sina
        fetch_fn = getattr(ak, "futures_zh_min_sina", None) or getattr(ak, "futures_zh_minute_sina", None)
        if fetch_fn is None:
            raise RuntimeError("AkShare has neither futures_zh_min_sina nor futures_zh_minute_sina")

        try:
            raw_df = fetch_fn(symbol=sym, period=period)
        except Exception as e:
            logger.warning(f"Error fetching hourly data for {sym}: {e}")
            raise

        if raw_df is None or raw_df.empty:
            logger.info(f"No hourly data returned for {sym}")
            return pd.DataFrame(columns=[
                "symbol", "datetime", "open", "high", "low", "close", "volume", "open_interest", "turnover"
            ])

        df = raw_df.copy()

        # Standardize column names
        rename_map = {
            "d": "datetime",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
            "p": "open_interest",
            "hold": "open_interest",
        }
        df = df.rename(columns=rename_map)

        # Standardize datetime format to YYYY-MM-DD HH:MM:SS
        df["datetime"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d %H:%M:%S")

        if "turnover" not in df.columns:
            df["turnover"] = 0.0

        if "open_interest" not in df.columns:
            df["open_interest"] = 0.0

        if "volume" not in df.columns:
            df["volume"] = 0.0

        # Cast numeric fields
        for col in ["open", "high", "low", "close", "volume", "open_interest", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Drop invalid OHLC
        df = df.dropna(subset=["datetime", "open", "high", "low", "close"])

        # Fill 0.0 for missing metrics
        df["volume"] = df["volume"].fillna(0.0)
        df["open_interest"] = df["open_interest"].fillna(0.0)
        df["turnover"] = df["turnover"].fillna(0.0)

        # Filter by history_days
        days = history_days if history_days is not None else self.history_days
        if days and days > 0:
            cutoff = self._get_cutoff_datetime(days)
            df = df[df["datetime"] >= cutoff]

        df["symbol"] = sym

        # Canonical column ordering
        cols = ["symbol", "datetime", "open", "high", "low", "close", "volume", "open_interest", "turnover"]
        df = df[cols].reset_index(drop=True)
        return df
