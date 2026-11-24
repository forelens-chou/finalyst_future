"""
Batch synchronization engine, rate-limiting, error isolation, and summary reporting.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from src.config import AppConfig
from src.fetcher import FuturesFetcher
from src.network import setup_proxy_environment
from src.storage import StorageManager
from src.symbols import get_active_symbols

logger = logging.getLogger("fetchfu.runner")


class SyncRunner:
    """
    Executes futures data collection with pacing, error isolation, and summary reporting.
    """

    def __init__(
        self,
        config: AppConfig,
        storage: Optional[StorageManager] = None,
        fetcher: Optional[FuturesFetcher] = None,
    ):
        self.config = config
        self.storage = storage or StorageManager.from_config(config)
        self.fetcher = fetcher or FuturesFetcher(config)

    def run(
        self,
        symbols: Optional[List[str] | str] = None,
        period: str = "all",
        history_days: Optional[int] = None,
        incremental: bool = False,
        resume: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute sync for active symbols.

        :param symbols: Symbol override (list or comma-separated string) or None.
        :param period: 'all' (both daily and hourly), '1d', or '60m'.
        :param history_days: History cutoff days override or None.
        :param incremental: If True, only fetch recent missing days (3-5 days) for existing symbols.
        :param resume: If True, skip symbols that already have complete data in database.
        :return: Execution summary dictionary.
        """
        # 1. Setup proxy environment if enabled
        setup_proxy_environment(self.config)

        # 2. Resolve target symbols
        target_symbols = get_active_symbols(self.config, symbol_override=symbols)
        if not target_symbols:
            logger.warning("No active symbols to sync.")
            return {
                "total_symbols": 0,
                "successful_symbols": 0,
                "failed_symbols": [],
                "daily_records": 0,
                "hourly_records": 0,
                "elapsed_seconds": 0.0,
            }

        period_lower = period.lower().strip()
        do_daily = period_lower in ("all", "1d", "daily")
        do_hourly = period_lower in ("all", "60m", "hourly")
        default_days = history_days if history_days is not None else self.config.fetch.history_days

        mode_str = "RESUME" if resume else ("INCREMENTAL" if incremental else "FULL")
        logger.info(
            f"Starting sync: symbols={len(target_symbols)}, period={period_lower}, "
            f"mode={mode_str}, default_days={default_days}, "
            f"pacing=[{self.config.fetch.pacing_min_seconds}s, {self.config.fetch.pacing_max_seconds}s]"
        )

        t_start = time.perf_counter()
        successful_symbols: List[str] = []
        failed_symbols: List[Tuple[str, str]] = []
        total_daily_records = 0
        total_hourly_records = 0

        for idx, sym in enumerate(target_symbols, 1):
            logger.info(f"[{idx}/{len(target_symbols)}] Processing {sym}...")
            sym_daily_records = 0
            sym_hourly_records = 0
            sym_failed = False
            last_err_msg = ""

            last_daily = self.storage.get_symbol_latest_datetime(sym, "daily_bars")
            last_hourly = self.storage.get_symbol_latest_datetime(sym, "hourly_bars")

            # Breakpoint resume check
            if resume:
                skip_daily = not do_daily or (last_daily is not None)
                skip_hourly = not do_hourly or (last_hourly is not None)
                if skip_daily and skip_hourly:
                    logger.info(f"[{sym}] Already exists in DB, skipping (--resume).")
                    successful_symbols.append(sym)
                    continue

            # Determine lookback days for daily and hourly
            daily_days = default_days
            hourly_days = default_days
            if incremental:
                if last_daily:
                    daily_days = 5  # Recent 5 days covers weekends/holidays safely
                if last_hourly:
                    hourly_days = 3  # Recent 3 days covers intraday updates

            # Sync Daily Bars
            if do_daily:
                try:
                    df_daily = self.fetcher.fetch_daily_bars(sym, history_days=daily_days)
                    if not df_daily.empty:
                        count = self.storage.upsert_daily_bars(sym, df_daily)
                        sym_daily_records += count
                except Exception as e:
                    sym_failed = True
                    last_err_msg = f"Daily error: {e}"
                    logger.error(f"[{sym}] Daily fetch failed: {e}")

            # Sync Hourly Bars
            if do_hourly and not sym_failed:
                try:
                    df_hourly = self.fetcher.fetch_hourly_bars(sym, history_days=hourly_days)
                    if not df_hourly.empty:
                        count = self.storage.upsert_hourly_bars(sym, df_hourly)
                        sym_hourly_records += count
                except Exception as e:
                    sym_failed = True
                    last_err_msg = f"Hourly error: {e}"
                    logger.error(f"[{sym}] Hourly fetch failed: {e}")

            if sym_failed:
                failed_symbols.append((sym, last_err_msg))
            else:
                successful_symbols.append(sym)
                total_daily_records += sym_daily_records
                total_hourly_records += sym_hourly_records
                logger.info(
                    f"[{sym}] OK: daily={sym_daily_records}, hourly={sym_hourly_records}"
                )

            # Pacing delay between symbols (except after last symbol)
            if idx < len(target_symbols):
                p_min = self.config.fetch.pacing_min_seconds
                p_max = self.config.fetch.pacing_max_seconds
                if p_max > 0:
                    sleep_sec = random.uniform(p_min, p_max)
                    time.sleep(sleep_sec)

        elapsed = round(time.perf_counter() - t_start, 2)
        summary = {
            "total_symbols": len(target_symbols),
            "successful_symbols": len(successful_symbols),
            "failed_symbols": [s[0] for s in failed_symbols],
            "failed_details": failed_symbols,
            "daily_records": total_daily_records,
            "hourly_records": total_hourly_records,
            "elapsed_seconds": elapsed,
        }

        self.print_summary_board(summary)
        return summary

    @staticmethod
    def print_summary_board(summary: Dict[str, Any]) -> None:
        """
        Print the standardized summary banner to console and logs.
        """
        total = summary["total_symbols"]
        success = summary["successful_symbols"]
        failed = len(summary["failed_symbols"])
        failed_list_str = (
            f" ({', '.join(summary['failed_symbols'])})" if failed > 0 else ""
        )
        daily_cnt = summary["daily_records"]
        hourly_cnt = summary["hourly_records"]
        elapsed = summary["elapsed_seconds"]

        board = [
            "",
            "========== 同步汇总看板 ==========",
            f"总目标品种数: {total}",
            f"成功品种数:   {success}",
            f"失败品种数:   {failed}{failed_list_str}",
            f"新增/更新记录: 日线 {daily_cnt:,} 条, 小时线 {hourly_cnt:,} 条",
            f"总耗时:       {elapsed:.1f} 秒",
            "==================================",
            "",
        ]
        board_text = "\n".join(board)
        logger.info(board_text)
        print(board_text)
