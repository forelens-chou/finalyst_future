"""
Feature engineering, indicator calculation, and volume normalization.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def get_bar_active_minutes(datetime_str: str) -> int:
    """
    Return active trading minutes for a domestic futures hourly bar.
    Avoids false volume divergence caused by partial trading sessions (e.g. 14:30-15:00).
    """
    try:
        parts = str(datetime_str).strip().split(" ")
        time_part = parts[1][:5] if len(parts) > 1 else parts[0][:5]
    except Exception:
        return 60

    if time_part in ("10:15", "10:30"):
        return 75  # 09:00 - 10:15
    elif time_part == "11:30":
        return 60  # 10:30 - 11:30
    elif time_part == "14:30":
        return 60  # 13:30 - 14:30
    elif time_part == "15:00":
        return 30  # 14:30 - 15:00 (half session)
    elif time_part in ("22:00", "23:00", "00:00", "01:00", "02:00"):
        return 60
    elif time_part == "02:30":
        return 30  # 02:00 - 02:30
    return 60


def normalize_hourly_volume(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add 'norm_vol' = volume / active_minutes * 60 to hourly DataFrame.
    """
    if df.empty or "volume" not in df.columns or "datetime" not in df.columns:
        return df

    out = df.copy()
    active_mins = out["datetime"].apply(get_bar_active_minutes)
    out["active_minutes"] = active_mins
    out["norm_vol"] = (out["volume"] / active_mins) * 60.0
    return out


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Average True Range (ATR).
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr


def compute_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """
    Compute MACD: dif, dea, macd_hist.
    Note: macd_hist = (dif - dea) * 2 in Chinese convention.
    """
    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2.0

    out = pd.DataFrame(index=df.index)
    out["dif"] = dif
    out["dea"] = dea
    out["macd_hist"] = hist
    return out


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Relative Strength Index (RSI).
    """
    close = df["close"]
    delta = close.diff()

    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Calculate Average Directional Index (ADX) and +DI / -DI.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    plus_dm = high - prev_high
    minus_dm = prev_low - low

    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    tr_smoothed = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    plus_dm_smoothed = pd.Series(plus_dm, index=df.index).ewm(
        alpha=1.0 / period, min_periods=period, adjust=False
    ).mean()
    minus_dm_smoothed = pd.Series(minus_dm, index=df.index).ewm(
        alpha=1.0 / period, min_periods=period, adjust=False
    ).mean()

    plus_di = (plus_dm_smoothed / tr_smoothed.replace(0.0, np.nan)) * 100.0
    minus_di = (minus_dm_smoothed / tr_smoothed.replace(0.0, np.nan)) * 100.0

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan) * 100.0
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    res = pd.DataFrame(index=df.index)
    res["plus_di"] = plus_di.fillna(0.0)
    res["minus_di"] = minus_di.fillna(0.0)
    res["adx"] = adx.fillna(0.0)
    return res


def compute_donchian_channels(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Compute Donchian Channel upper and lower bands.
    """
    res = pd.DataFrame(index=df.index)
    res["donchian_upper"] = df["high"].rolling(window=period).max()
    res["donchian_lower"] = df["low"].rolling(window=period).min()
    return res


def compute_ema(series: pd.Series, period: int = 20) -> pd.Series:
    """
    Compute Exponential Moving Average.
    """
    return series.ewm(span=period, adjust=False).mean()
