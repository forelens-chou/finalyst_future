"""
Daily Screener: Identifies high-potential candidate symbols approaching key S/R zones.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import pandas as pd

from src.config import InstrumentMeta, StrategyConfig
from src.data_loader import DataLoader
from src.filters import check_adx_trend_filter
from src.preprocessor import compute_adx, compute_atr, compute_donchian_channels, compute_macd
from src.sr_zone import (
    calculate_daily_fibonacci_space,
    cluster_sr_zones,
    compute_atr_zigzag,
    find_nearest_sr_zone,
)

logger = logging.getLogger("divergence.screener")


class DailyScreener:
    """
    Scans the daily market data to identify symbols near significant S/R zones with favorable R:R space.
    """

    def __init__(self, config: StrategyConfig, data_loader: Optional[DataLoader] = None):
        self.config = config
        self.loader = data_loader or DataLoader(config)

    def scan_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Screen a single symbol for daily S/R proximity and space.
        """
        sym = symbol.upper().strip()
        df_daily = self.loader.get_daily_bars(sym, limit_days=self.config.history_days_daily)
        if len(df_daily) < 30:
            return None

        meta = self.config.instruments.get(sym, InstrumentMeta(symbol=sym))

        # 1. Indicator calculation
        atr_series = compute_atr(df_daily, period=self.config.screener.atr_period)
        cur_atr = float(atr_series.iloc[-1])
        if cur_atr <= 0 or pd.isna(cur_atr):
            return None

        current_price = float(df_daily["close"].iloc[-1])
        last_date = str(df_daily["datetime"].iloc[-1])

        # 2. ATR-ZigZag & S/R Zones
        swings = compute_atr_zigzag(
            df_daily, atr_series, atr_multiplier=self.config.screener.zigzag_atr_multiplier
        )
        zones = cluster_sr_zones(
            swings,
            cur_atr,
            merge_atr_multiplier=self.config.screener.zone_merge_atr_multiplier,
            zone_width_atr_multiplier=self.config.screener.zone_width_atr_multiplier,
        )

        if not zones:
            return None

        # 3. Find closest S/R zone
        max_dist = self.config.screener.near_zone_atr_multiplier * cur_atr
        res = find_nearest_sr_zone(current_price, zones, max_distance=max_dist)
        if not res:
            return None

        zone, distance = res

        # Expected direction:
        # If near RESISTANCE -> Expect SHORT reversal
        # If near SUPPORT -> Expect LONG reversal
        expected_dir = "SHORT" if zone.zone_type == "RESISTANCE" else "LONG"

        # 4. Check ADX trend filter on daily
        adx_df = compute_adx(df_daily, period=self.config.filters.adx.period)
        adx_res = check_adx_trend_filter(df_daily, adx_df, expected_dir, self.config.filters)
        if not adx_res["passed"]:
            logger.debug(f"[{sym}] Daily screener: {adx_res['reason']}")
            return None

        # 5. Check space to 38.2% Fibonacci retracement
        tp1_382, tp2_667, space_to_tp1 = calculate_daily_fibonacci_space(
            current_price, swings, zone.zone_type
        )

        # Approximate 1H ATR as ~0.35 of daily ATR
        est_1h_atr = cur_atr * 0.35
        min_required_space = self.config.screener.min_space_to_tp1_atr_multiplier * est_1h_atr
        if space_to_tp1 < min_required_space:
            logger.debug(f"[{sym}] Insufficient space to TP1 ({space_to_tp1:.1f} < {min_required_space:.1f})")
            return None

        # 6. Check for Daily MACD divergence bonus (S-Class priority)
        macd_df = compute_macd(df_daily)
        daily_dif = float(macd_df["dif"].iloc[-1])
        daily_dif_prev = float(macd_df["dif"].iloc[-5])
        is_s_class = False
        if expected_dir == "SHORT" and current_price >= df_daily["high"].iloc[-20:].max() * 0.99:
            if daily_dif < daily_dif_prev:
                is_s_class = True
        elif expected_dir == "LONG" and current_price <= df_daily["low"].iloc[-20:].min() * 1.01:
            if daily_dif > daily_dif_prev:
                is_s_class = True

        return {
            "symbol": sym,
            "name": meta.name,
            "sector": meta.sector,
            "last_date": last_date,
            "current_price": round(current_price, 2),
            "zone_type": zone.zone_type,
            "zone_center": zone.center_price,
            "zone_bounds": [zone.lower_bound, zone.upper_bound],
            "distance_to_zone": round(distance, 2),
            "expected_direction": expected_dir,
            "daily_atr": round(cur_atr, 2),
            "est_1h_atr": round(est_1h_atr, 2),
            "tp1_382": tp1_382,
            "tp2_667": tp2_667,
            "space_to_tp1": space_to_tp1,
            "adx_1d": adx_res["adx_value"],
            "is_s_class": is_s_class,
            "priority": "S_TIER" if is_s_class else "A_TIER",
        }

    def run_all(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Run daily screener across symbols.
        """
        target_symbols = symbols or self.loader.get_available_symbols()
        candidates = []
        for sym in target_symbols:
            try:
                res = self.scan_symbol(sym)
                if res:
                    candidates.append(res)
            except Exception as e:
                logger.error(f"Error screening {sym}: {e}")
                continue

        # Sort: S_TIER first, then by distance to zone ascending
        candidates.sort(key=lambda x: (0 if x["priority"] == "S_TIER" else 1, x["distance_to_zone"]))
        return candidates
