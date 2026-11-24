"""
Hourly Scanner: Scans 1H bar data, detects divergences, and generates executable trade plans.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import pandas as pd

from src.config import InstrumentMeta, StrategyConfig
from src.data_loader import DataLoader
from src.divergence import (
    check_bearish_divergence,
    check_bullish_divergence,
    find_local_extrema_1h,
)
from src.filters import check_market_close_risk
from src.preprocessor import (
    compute_atr,
    compute_macd,
    compute_rsi,
    normalize_hourly_volume,
)
from src.risk_manager import generate_trade_plan

logger = logging.getLogger("divergence.scanner")


class HourlyScanner:
    """
    Executes hourly scans for multi-indicator divergence and produces concrete trade plans.
    """

    def __init__(self, config: StrategyConfig, data_loader: Optional[DataLoader] = None):
        self.config = config
        self.loader = data_loader or DataLoader(config)

    def scan_symbol(
        self,
        symbol: str,
        daily_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Scan a single symbol's 1H data for divergence signals.
        """
        sym = symbol.upper().strip()
        df_1h = self.loader.get_hourly_bars(sym, limit_days=self.config.history_days_hourly)
        if len(df_1h) < self.config.scanner.lookback_bars:
            return None

        meta = self.config.instruments.get(sym, InstrumentMeta(symbol=sym))

        # 1. Preprocess & Compute Indicators on 1H
        df_1h = normalize_hourly_volume(df_1h)
        atr_1h = compute_atr(df_1h, period=14)
        cur_1h_atr = float(atr_1h.iloc[-1])
        if cur_1h_atr <= 0 or pd.isna(cur_1h_atr):
            return None

        macd_df = compute_macd(
            df_1h,
            fast=self.config.scanner.macd.fast_period,
            slow=self.config.scanner.macd.slow_period,
            signal=self.config.scanner.macd.signal_period,
        )
        df_1h["dif"] = macd_df["dif"]
        df_1h["dea"] = macd_df["dea"]
        df_1h["macd_hist"] = macd_df["macd_hist"]

        rsi_series = compute_rsi(df_1h, period=self.config.scanner.rsi.period)
        df_1h["rsi"] = rsi_series

        current_price = float(df_1h["close"].iloc[-1])
        last_dt = str(df_1h["datetime"].iloc[-1])

        # 2. Local extrema in lookback window
        window_df = df_1h.iloc[-self.config.scanner.lookback_bars :].reset_index(drop=True)
        peaks, troughs = find_local_extrema_1h(window_df, window=3)

        # 3. Detect Divergence
        # If daily_context not provided, attempt to auto-fetch daily context on the fly
        if daily_context is None:
            try:
                from src.daily_screener import DailyScreener
                screener = DailyScreener(self.config, self.loader)
                daily_context = screener.scan_symbol(sym)
            except Exception:
                daily_context = None

        expected_dir = daily_context.get("expected_direction") if daily_context else None

        bear_res = None
        bull_res = None

        if expected_dir in (None, "SHORT"):
            bear_res = check_bearish_divergence(
                window_df, peaks, troughs, cur_1h_atr, self.config.scanner
            )

        if expected_dir in (None, "LONG"):
            bull_res = check_bullish_divergence(
                window_df, peaks, troughs, cur_1h_atr, self.config.scanner
            )

        div_res = bear_res or bull_res
        if not div_res or not div_res.has_divergence:
            return None

        # 4. Check Market Close Proximity Risk
        close_risk = check_market_close_risk(
            last_dt, buffer_minutes=self.config.filters.market_close_buffer_minutes
        )

        # 5. Extract TP anchors from daily context if available
        tp1_daily = daily_context.get("tp1_382") if daily_context else None
        tp2_daily = daily_context.get("tp2_667") if daily_context else None

        # 6. Generate Trade Plan
        plan = generate_trade_plan(
            div_result=div_res,
            current_price=current_price,
            current_1h_atr=cur_1h_atr,
            meta=meta,
            risk_config=self.config.risk,
            daily_tp1_382=tp1_daily,
            daily_tp2_667=tp2_daily,
        )

        if not plan:
            return None

        return {
            "timestamp": last_dt,
            "symbol": sym,
            "name": meta.name,
            "sector": meta.sector,
            "direction": div_res.direction,
            "score": div_res.score,
            "resonance_level": div_res.resonance_level,
            "divergence_details": div_res.details,
            "p1": {"price": div_res.p1.price, "time": div_res.p1.datetime},
            "p2": {"price": div_res.p2.price, "time": div_res.p2.datetime},
            "v_mid": {"price": div_res.v_mid.price, "time": div_res.v_mid.datetime},
            "close_risk": close_risk,
            "trade_plan": plan,
            "daily_context": daily_context or {},
        }

    def run_all(
        self,
        symbols: Optional[List[str]] = None,
        daily_candidates_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run hourly scanner for target symbols.
        """
        target_symbols = symbols or self.loader.get_available_symbols()
        signals = []

        for sym in target_symbols:
            ctx = daily_candidates_map.get(sym) if daily_candidates_map else None
            try:
                sig = self.scan_symbol(sym, daily_context=ctx)
                if sig:
                    signals.append(sig)
            except Exception as e:
                logger.error(f"Error scanning hourly for {sym}: {e}")
                continue

        # Sort signals by resonance level desc, then score desc
        signals.sort(key=lambda s: (s["resonance_level"], s["score"]), reverse=True)
        return signals
