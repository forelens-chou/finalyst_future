import pandas as pd
import pytest

from src.config import HourlyScannerConfig
from src.divergence import (
    PeakExtremum,
    check_bearish_divergence,
    check_bullish_divergence,
)


def test_bearish_divergence_logic():
    config = HourlyScannerConfig()
    config.min_peak_distance = 5

    # Peaks: P1 at index 10 (price 3500, dif 20), P2 at index 20 (price 3600, dif 15)
    # Trough: V_mid at index 15 (price 3400)
    p1 = PeakExtremum(10, "2026-11-20 10:00:00", 3500.0, dif=20.0, macd_hist=10.0, rsi=70.0, norm_vol=1000.0, open_interest=10000.0)
    p2 = PeakExtremum(20, "2026-11-20 15:00:00", 3600.0, dif=15.0, macd_hist=8.0, rsi=64.0, norm_vol=800.0, open_interest=9500.0)
    v_mid = PeakExtremum(15, "2026-11-20 13:00:00", 3400.0, dif=5.0, macd_hist=-2.0, rsi=45.0, norm_vol=600.0, open_interest=9800.0)

    # Synthetic df with macd_hist
    hist_vals = [5.0] * 25
    hist_vals[10] = 10.0
    hist_vals[15] = -2.0
    hist_vals[20] = 8.0
    df = pd.DataFrame({
        "macd_hist": hist_vals,
        "norm_vol": [800.0] * 25,
        "volume": [800.0] * 25,
        "datetime": [f"t_{i}" for i in range(25)],
    })

    res = check_bearish_divergence(
        df=df,
        peaks=[p1, p2],
        troughs=[v_mid],
        current_atr=20.0,
        config=config,
    )

    assert res is not None
    assert res.has_divergence is True
    assert res.direction == "SHORT"
    assert res.macd_double_div is True
    assert res.macd_cross_zero is True
    assert res.rsi_div is True
    assert res.oi_div is True
    assert res.resonance_level == 3
    assert res.score >= 0.8
