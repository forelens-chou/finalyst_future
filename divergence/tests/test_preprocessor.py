import numpy as np
import pandas as pd
import pytest

from src.preprocessor import (
    compute_adx,
    compute_atr,
    compute_macd,
    compute_rsi,
    get_bar_active_minutes,
    normalize_hourly_volume,
)


def test_active_minutes_mapping():
    assert get_bar_active_minutes("2026-11-20 15:00:00") == 30
    assert get_bar_active_minutes("2026-11-20 11:30:00") == 60
    assert get_bar_active_minutes("2026-11-20 10:15:00") == 75
    assert get_bar_active_minutes("2026-11-20 02:30:00") == 30
    assert get_bar_active_minutes("2026-11-20 22:00:00") == 60


def test_normalize_hourly_volume():
    df = pd.DataFrame({
        "datetime": ["2026-11-20 14:30:00", "2026-11-20 15:00:00"],
        "volume": [1000.0, 500.0],
    })
    norm_df = normalize_hourly_volume(df)
    # 14:30 active_mins = 60 -> norm_vol = 1000
    # 15:00 active_mins = 30 -> norm_vol = 500 / 30 * 60 = 1000
    assert pytest.approx(norm_df["norm_vol"].iloc[0], rel=1e-5) == 1000.0
    assert pytest.approx(norm_df["norm_vol"].iloc[1], rel=1e-5) == 1000.0


def test_indicator_calculations():
    # Synthetic trending data
    n = 50
    close = np.linspace(3000, 3500, n) + np.sin(np.linspace(0, 10, n)) * 20
    high = close + 15
    low = close - 15
    df = pd.DataFrame({"high": high, "low": low, "close": close})

    atr = compute_atr(df, period=14)
    assert len(atr) == n
    assert atr.iloc[-1] > 0

    macd_df = compute_macd(df)
    assert "dif" in macd_df.columns
    assert "dea" in macd_df.columns
    assert "macd_hist" in macd_df.columns

    rsi = compute_rsi(df, period=14)
    assert 0 <= rsi.iloc[-1] <= 100

    adx_df = compute_adx(df, period=14)
    assert "adx" in adx_df.columns
    assert adx_df["adx"].iloc[-1] >= 0
