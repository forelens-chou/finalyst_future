"""
ATR-ZigZag Support / Resistance Zone detection and Fibonacci space calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
import numpy as np
import pandas as pd


@dataclass
class SwingPoint:
    index: int
    datetime: str
    price: float
    point_type: str  # 'HIGH' or 'LOW'


@dataclass
class SRZone:
    zone_type: str  # 'RESISTANCE' or 'SUPPORT'
    center_price: float
    lower_bound: float
    upper_bound: float
    touch_count: int
    points: List[SwingPoint]

    def contains(self, price: float) -> bool:
        return self.lower_bound <= price <= self.upper_bound

    def distance_to(self, price: float) -> float:
        if self.contains(price):
            return 0.0
        if price < self.lower_bound:
            return self.lower_bound - price
        return price - self.upper_bound


def compute_atr_zigzag(
    df: pd.DataFrame,
    atr_series: pd.Series,
    atr_multiplier: float = 3.0,
) -> List[SwingPoint]:
    """
    Identify significant wave swing highs and lows using dynamic ATR threshold.
    """
    if len(df) < 5:
        return []

    highs = df["high"].values
    lows = df["low"].values
    dates = df["datetime"].values
    atrs = atr_series.bfill().fillna(0.0).values

    swings: List[SwingPoint] = []

    # State tracking: 1 for searching high, -1 for searching low
    last_high_idx = 0
    last_low_idx = 0
    trend = 0  # 1: up, -1: down

    for i in range(1, len(df)):
        cur_atr = atrs[i] if not np.isnan(atrs[i]) else atrs[-1]
        threshold = cur_atr * atr_multiplier

        if trend == 0:
            if highs[i] - lows[last_low_idx] >= threshold:
                trend = 1
                last_high_idx = i
                swings.append(SwingPoint(i, str(dates[i]), float(highs[i]), "HIGH"))
            elif highs[last_high_idx] - lows[i] >= threshold:
                trend = -1
                last_low_idx = i
                swings.append(SwingPoint(i, str(dates[i]), float(lows[i]), "LOW"))
        elif trend == 1:
            # Continuing up: if higher high found, update high
            if highs[i] > highs[last_high_idx]:
                last_high_idx = i
                if swings and swings[-1].point_type == "HIGH":
                    swings[-1] = SwingPoint(i, str(dates[i]), float(highs[i]), "HIGH")
            # If reversal down exceeds threshold
            elif highs[last_high_idx] - lows[i] >= threshold:
                trend = -1
                last_low_idx = i
                swings.append(SwingPoint(i, str(dates[i]), float(lows[i]), "LOW"))
        elif trend == -1:
            # Continuing down: if lower low found, update low
            if lows[i] < lows[last_low_idx]:
                last_low_idx = i
                if swings and swings[-1].point_type == "LOW":
                    swings[-1] = SwingPoint(i, str(dates[i]), float(lows[i]), "LOW")
            # If reversal up exceeds threshold
            elif highs[i] - lows[last_low_idx] >= threshold:
                trend = 1
                last_high_idx = i
                swings.append(SwingPoint(i, str(dates[i]), float(highs[i]), "HIGH"))

    return swings


def cluster_sr_zones(
    swing_points: List[SwingPoint],
    current_atr: float,
    merge_atr_multiplier: float = 0.5,
    zone_width_atr_multiplier: float = 0.3,
) -> List[SRZone]:
    """
    Cluster adjacent swing points within merge_atr_multiplier * ATR into S/R heat zones.
    """
    if not swing_points or current_atr <= 0:
        return []

    high_points = [p for p in swing_points if p.point_type == "HIGH"]
    low_points = [p for p in swing_points if p.point_type == "LOW"]

    zones: List[SRZone] = []

    for point_list, z_type in [(high_points, "RESISTANCE"), (low_points, "SUPPORT")]:
        if not point_list:
            continue
        sorted_points = sorted(point_list, key=lambda x: x.price)
        clusters: List[List[SwingPoint]] = [[sorted_points[0]]]

        for pt in sorted_points[1:]:
            prev_cluster = clusters[-1]
            cluster_mean = sum(p.price for p in prev_cluster) / len(prev_cluster)
            if abs(pt.price - cluster_mean) <= merge_atr_multiplier * current_atr:
                prev_cluster.append(pt)
            else:
                clusters.append([pt])

        for c in clusters:
            center = sum(p.price for p in c) / len(c)
            width = zone_width_atr_multiplier * current_atr
            zones.append(
                SRZone(
                    zone_type=z_type,
                    center_price=round(center, 2),
                    lower_bound=round(center - width, 2),
                    upper_bound=round(center + width, 2),
                    touch_count=len(c),
                    points=c,
                )
            )

    return zones


def find_nearest_sr_zone(
    current_price: float,
    zones: List[SRZone],
    max_distance: float,
) -> Optional[Tuple[SRZone, float]]:
    """
    Find the closest S/R zone within max_distance.
    """
    best_zone: Optional[SRZone] = None
    min_dist = float("inf")

    for z in zones:
        dist = z.distance_to(current_price)
        if dist <= max_distance and dist < min_dist:
            min_dist = dist
            best_zone = z

    if best_zone:
        return best_zone, min_dist
    return None


def calculate_daily_fibonacci_space(
    current_price: float,
    recent_swings: List[SwingPoint],
    zone_type: str,
) -> Tuple[float, float, float]:
    """
    Calculate 38.2% and 66.7% Fibonacci retracement from trend anchor,
    and return (tp1_382, tp2_667, space_to_tp1).
    """
    if len(recent_swings) < 2:
        # Fallback anchor: ±5%
        tp1 = current_price * 0.96 if zone_type == "RESISTANCE" else current_price * 1.04
        tp2 = current_price * 0.94 if zone_type == "RESISTANCE" else current_price * 1.06
        return round(tp1, 2), round(tp2, 2), abs(current_price - tp1)

    if zone_type == "RESISTANCE":
        # Upward trend approaching resistance: anchor from lowest swing low to highest swing high
        lows = [p.price for p in recent_swings if p.point_type == "LOW"]
        highs = [p.price for p in recent_swings if p.point_type == "HIGH"]
        anchor_low = min(lows) if lows else current_price * 0.90
        anchor_high = max(highs) if highs else current_price

        span = anchor_high - anchor_low
        tp1 = anchor_high - 0.382 * span
        tp2 = anchor_high - 0.667 * span
        space = abs(current_price - tp1)
        return round(tp1, 2), round(tp2, 2), round(space, 2)
    else:
        # Downward trend approaching support: anchor from highest swing high to lowest swing low
        highs = [p.price for p in recent_swings if p.point_type == "HIGH"]
        lows = [p.price for p in recent_swings if p.point_type == "LOW"]
        anchor_high = max(highs) if highs else current_price * 1.10
        anchor_low = min(lows) if lows else current_price

        span = anchor_high - anchor_low
        tp1 = anchor_low + 0.382 * span
        tp2 = anchor_low + 0.667 * span
        space = abs(current_price - tp1)
        return round(tp1, 2), round(tp2, 2), round(space, 2)
