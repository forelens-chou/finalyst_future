"""
Multi-indicator divergence detection: MACD double divergence, RSI, normalized volume, and Open Interest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from src.config import HourlyScannerConfig


@dataclass
class PeakExtremum:
    index: int
    datetime: str
    price: float
    dif: float
    macd_hist: float
    rsi: float
    norm_vol: float
    open_interest: float


@dataclass
class DivergenceResult:
    has_divergence: bool
    direction: str  # 'SHORT' (bearish/top divergence) or 'LONG' (bullish/bottom divergence)
    p1: PeakExtremum
    p2: PeakExtremum
    v_mid: PeakExtremum
    macd_double_div: bool
    macd_cross_zero: bool
    rsi_div: bool
    volume_div: bool
    oi_div: bool
    score: float
    resonance_level: int  # 1, 2, or 3
    details: Dict[str, bool]


def find_local_extrema_1h(
    df: pd.DataFrame,
    window: int = 3,
) -> Tuple[List[PeakExtremum], List[PeakExtremum]]:
    """
    Locate local peaks and troughs using rolling window extrema.
    """
    highs = df["high"].values
    lows = df["low"].values
    difs = df["dif"].values
    hists = df["macd_hist"].values
    rsis = df["rsi"].values if "rsi" in df.columns else np.full(len(df), 50.0)
    vols = df["norm_vol"].values if "norm_vol" in df.columns else df["volume"].values
    ois = df["open_interest"].values if "open_interest" in df.columns else np.zeros(len(df))
    dts = df["datetime"].values

    peaks: List[PeakExtremum] = []
    troughs: List[PeakExtremum] = []
    n = len(df)

    for i in range(window, n - window):
        # Local peak
        if all(highs[i] >= highs[i - k] for k in range(1, window + 1)) and all(
            highs[i] >= highs[i + k] for k in range(1, window + 1)
        ):
            peaks.append(
                PeakExtremum(
                    index=i,
                    datetime=str(dts[i]),
                    price=float(highs[i]),
                    dif=float(difs[i]),
                    macd_hist=float(hists[i]),
                    rsi=float(rsis[i]),
                    norm_vol=float(vols[i]),
                    open_interest=float(ois[i]),
                )
            )

        # Local trough
        if all(lows[i] <= lows[i - k] for k in range(1, window + 1)) and all(
            lows[i] <= lows[i + k] for k in range(1, window + 1)
        ):
            troughs.append(
                PeakExtremum(
                    index=i,
                    datetime=str(dts[i]),
                    price=float(lows[i]),
                    dif=float(difs[i]),
                    macd_hist=float(hists[i]),
                    rsi=float(rsis[i]),
                    norm_vol=float(vols[i]),
                    open_interest=float(ois[i]),
                )
            )

    return peaks, troughs


def check_bearish_divergence(
    df: pd.DataFrame,
    peaks: List[PeakExtremum],
    troughs: List[PeakExtremum],
    current_atr: float,
    config: HourlyScannerConfig,
) -> Optional[DivergenceResult]:
    """
    Check for top / bearish divergence (for SHORT trade plans).
    """
    if len(peaks) < 2 or len(troughs) < 1:
        return None

    # Consider the latest two peaks
    p2 = peaks[-1]
    p1 = peaks[-2]

    # Constraint: minimum bar distance
    if (p2.index - p1.index) < config.min_peak_distance:
        return None

    # Intermediate trough between p1 and p2
    mid_troughs = [t for t in troughs if p1.index < t.index < p2.index]
    if not mid_troughs:
        return None
    v_mid = min(mid_troughs, key=lambda t: t.price)

    # Constraint: trough depth must be >= min_trough_depth_atr_multiplier * ATR
    trough_depth = min(p1.price, p2.price) - v_mid.price
    if trough_depth < config.min_trough_depth_atr_multiplier * current_atr:
        return None

    # Price must make higher high (or equal high)
    if p2.price <= p1.price:
        return None

    # 1. MACD Double Divergence: DIF(P2) < DIF(P1) AND Hist Peak(P2) < Hist Peak(P1)
    dif_div = p2.dif < p1.dif
    hist_peak_1 = df["macd_hist"].iloc[max(0, p1.index - 2) : min(len(df), p1.index + 3)].max()
    hist_peak_2 = df["macd_hist"].iloc[max(0, p2.index - 2) : min(len(df), p2.index + 3)].max()
    hist_div = hist_peak_2 < hist_peak_1

    if config.macd.require_double_divergence:
        if not (dif_div and hist_div):
            return None
    else:
        if not dif_div:
            return None

    # Bonus: Hist crossed below zero between p1 and p2
    hist_between = df["macd_hist"].iloc[p1.index : p2.index]
    crossed_zero = bool((hist_between < 0.0).any())

    # 2. RSI Divergence
    rsi_div = False
    if config.rsi.enabled:
        rsi_div = (p2.rsi < p1.rsi) and (p1.rsi >= config.rsi.overbought)

    # 3. Volume Divergence (normalized 3-bar mean around peaks)
    vol_div = False
    if config.volume.enabled:
        w = config.volume.window_bars
        vol_p1 = df["norm_vol"].iloc[max(0, p1.index - w) : min(len(df), p1.index + w + 1)].mean()
        vol_p2 = df["norm_vol"].iloc[max(0, p2.index - w) : min(len(df), p2.index + w + 1)].mean()
        if vol_p1 > 0:
            vol_div = vol_p2 < (vol_p1 * config.volume.shrink_ratio)

    # 4. Open Interest Divergence (short-covering / buying exhaustion)
    oi_div = False
    if config.open_interest.enabled and p1.open_interest > 0:
        oi_div = p2.open_interest <= (p1.open_interest * config.open_interest.max_oi_ratio)

    # Calculate resonance score and level
    score = 0.50  # Base for MACD double divergence
    if crossed_zero:
        score += 0.10
    if rsi_div:
        score += 0.15
    if vol_div:
        score += 0.10
    if oi_div:
        score += 0.15
    score = min(1.0, round(score, 2))

    resonance_level = 1
    if (rsi_div or vol_div) and not oi_div:
        resonance_level = 2
    elif oi_div and (rsi_div or vol_div):
        resonance_level = 3
    elif oi_div:
        resonance_level = 2

    return DivergenceResult(
        has_divergence=True,
        direction="SHORT",
        p1=p1,
        p2=p2,
        v_mid=v_mid,
        macd_double_div=True,
        macd_cross_zero=crossed_zero,
        rsi_div=rsi_div,
        volume_div=vol_div,
        oi_div=oi_div,
        score=score,
        resonance_level=resonance_level,
        details={
            "macd_double_divergence": True,
            "macd_cross_zero": crossed_zero,
            "rsi_divergence": rsi_div,
            "volume_divergence": vol_div,
            "oi_divergence": oi_div,
        },
    )


def check_bullish_divergence(
    df: pd.DataFrame,
    peaks: List[PeakExtremum],
    troughs: List[PeakExtremum],
    current_atr: float,
    config: HourlyScannerConfig,
) -> Optional[DivergenceResult]:
    """
    Check for bottom / bullish divergence (for LONG trade plans).
    """
    if len(troughs) < 2 or len(peaks) < 1:
        return None

    # Consider the latest two troughs
    t2 = troughs[-1]
    t1 = troughs[-2]

    if (t2.index - t1.index) < config.min_peak_distance:
        return None

    mid_peaks = [p for p in peaks if t1.index < p.index < t2.index]
    if not mid_peaks:
        return None
    v_mid = max(mid_peaks, key=lambda p: p.price)

    peak_rise = v_mid.price - max(t1.price, t2.price)
    if peak_rise < config.min_trough_depth_atr_multiplier * current_atr:
        return None

    # Price makes lower low
    if t2.price >= t1.price:
        return None

    # 1. MACD Bullish Divergence: DIF(T2) > DIF(T1) AND Hist Valley(T2) > Hist Valley(T1)
    dif_div = t2.dif > t1.dif
    hist_valley_1 = df["macd_hist"].iloc[max(0, t1.index - 2) : min(len(df), t1.index + 3)].min()
    hist_valley_2 = df["macd_hist"].iloc[max(0, t2.index - 2) : min(len(df), t2.index + 3)].min()
    hist_div = hist_valley_2 > hist_valley_1

    if config.macd.require_double_divergence:
        if not (dif_div and hist_div):
            return None
    else:
        if not dif_div:
            return None

    hist_between = df["macd_hist"].iloc[t1.index : t2.index]
    crossed_zero = bool((hist_between > 0.0).any())

    # 2. RSI Divergence
    rsi_div = False
    if config.rsi.enabled:
        rsi_div = (t2.rsi > t1.rsi) and (t1.rsi <= config.rsi.oversold)

    # 3. Volume Divergence
    vol_div = False
    if config.volume.enabled:
        w = config.volume.window_bars
        vol_t1 = df["norm_vol"].iloc[max(0, t1.index - w) : min(len(df), t1.index + w + 1)].mean()
        vol_t2 = df["norm_vol"].iloc[max(0, t2.index - w) : min(len(df), t2.index + w + 1)].mean()
        if vol_t1 > 0:
            vol_div = vol_t2 < (vol_t1 * config.volume.shrink_ratio)

    # 4. Open Interest Divergence
    oi_div = False
    if config.open_interest.enabled and t1.open_interest > 0:
        oi_div = t2.open_interest <= (t1.open_interest * config.open_interest.max_oi_ratio)

    score = 0.50
    if crossed_zero:
        score += 0.10
    if rsi_div:
        score += 0.15
    if vol_div:
        score += 0.10
    if oi_div:
        score += 0.15
    score = min(1.0, round(score, 2))

    resonance_level = 1
    if (rsi_div or vol_div) and not oi_div:
        resonance_level = 2
    elif oi_div and (rsi_div or vol_div):
        resonance_level = 3
    elif oi_div:
        resonance_level = 2

    return DivergenceResult(
        has_divergence=True,
        direction="LONG",
        p1=t1,
        p2=t2,
        v_mid=v_mid,
        macd_double_div=True,
        macd_cross_zero=crossed_zero,
        rsi_div=rsi_div,
        volume_div=vol_div,
        oi_div=oi_div,
        score=score,
        resonance_level=resonance_level,
        details={
            "macd_double_divergence": True,
            "macd_cross_zero": crossed_zero,
            "rsi_divergence": rsi_div,
            "volume_divergence": vol_div,
            "oi_divergence": oi_div,
        },
    )
