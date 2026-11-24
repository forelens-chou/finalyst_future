import numpy as np
import pandas as pd
import pytest

from src.sr_zone import (
    SRZone,
    SwingPoint,
    calculate_daily_fibonacci_space,
    cluster_sr_zones,
    compute_atr_zigzag,
    find_nearest_sr_zone,
)


def test_zigzag_and_clustering():
    # Construct a synthetic wave pattern: 100 -> 130 -> 110 -> 140 -> 115
    dates = [f"2026-01-{i:02d}" for i in range(1, 21)]
    prices = [100, 105, 115, 125, 130, 125, 120, 115, 110, 115, 125, 135, 140, 135, 130, 125, 120, 115, 116, 117]
    df = pd.DataFrame({
        "datetime": dates,
        "high": [p + 2 for p in prices],
        "low": [p - 2 for p in prices],
        "close": prices,
    })
    atr_series = pd.Series([5.0] * len(df))

    swings = compute_atr_zigzag(df, atr_series, atr_multiplier=2.0)
    assert len(swings) >= 2

    zones = cluster_sr_zones(swings, current_atr=5.0, merge_atr_multiplier=0.5, zone_width_atr_multiplier=0.3)
    assert len(zones) >= 1

    # Check nearest zone finding
    res = find_nearest_sr_zone(128.0, zones, max_distance=10.0)
    assert res is not None
    zone, dist = res
    assert dist >= 0


def test_fibonacci_space_calculation():
    swings = [
        SwingPoint(0, "2026-01-01", 3000.0, "LOW"),
        SwingPoint(5, "2026-01-06", 3500.0, "HIGH"),
        SwingPoint(10, "2026-01-11", 3200.0, "LOW"),
        SwingPoint(15, "2026-01-16", 3600.0, "HIGH"),
    ]
    # Resistance zone (approaching top): anchor low is 3000, high is 3600, span is 600
    # TP1 (38.2%) = 3600 - 0.382 * 600 = 3370.8
    tp1, tp2, space = calculate_daily_fibonacci_space(3580.0, swings, "RESISTANCE")
    assert tp1 < 3580.0
    assert tp2 < tp1
    assert space > 0
