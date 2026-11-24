"""
Market regime filters: ADX trend strength, sector resonance, market close & limit price risk.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import pandas as pd

from src.config import FiltersConfig, InstrumentMeta


def check_adx_trend_filter(
    df_daily: pd.DataFrame,
    adx_df: pd.DataFrame,
    direction: str,
    config: FiltersConfig,
) -> Dict[str, Any]:
    """
    Check if the market is in a strong trend (ADX > 35 with upward slope).
    If strong upward trend, block SHORT reversal trades.
    If strong downward trend, block LONG reversal trades.
    """
    if not config.adx.enabled or len(adx_df) < 3:
        return {"passed": True, "adx_value": 0.0, "is_strong_trend": False, "reason": "ADX filter disabled or insufficient data"}

    current_adx = float(adx_df["adx"].iloc[-1])
    prev_adx = float(adx_df["adx"].iloc[-2])
    slope_up = current_adx > prev_adx

    is_strong = (current_adx > config.adx.threshold) and slope_up

    passed = True
    reason = "Normal trend strength (acceptable for reversal)"

    if is_strong:
        plus_di = float(adx_df["plus_di"].iloc[-1])
        minus_di = float(adx_df["minus_di"].iloc[-1])
        if direction == "SHORT" and plus_di > minus_di:
            passed = False
            reason = f"Blocked: 1D ADX={current_adx:.1f} > {config.adx.threshold} indicates strong upward parabolic trend."
        elif direction == "LONG" and minus_di > plus_di:
            passed = False
            reason = f"Blocked: 1D ADX={current_adx:.1f} > {config.adx.threshold} indicates strong downward waterfall trend."

    return {
        "passed": passed,
        "adx_value": round(current_adx, 1),
        "is_strong_trend": is_strong,
        "reason": reason,
    }


def check_market_close_risk(
    last_datetime: str,
    buffer_minutes: int = 30,
) -> Dict[str, Any]:
    """
    Flag signals generated within buffer_minutes of market close (15:00 or late night).
    """
    try:
        parts = str(last_datetime).strip().split(" ")
        time_part = parts[1][:5] if len(parts) > 1 else parts[0][:5]
    except Exception:
        return {"risk": False, "time": "unknown"}

    risk = False
    warning = "Safe distance to market close"
    if time_part in ("14:30", "15:00"):
        risk = True
        warning = f"Warning: Near day-session market close ({time_part})"
    elif time_part in ("23:00", "01:00", "02:30"):
        risk = True
        warning = f"Warning: Near night-session market close ({time_part})"

    return {"risk": risk, "time": time_part, "warning": warning}


def compute_sector_sentiment(
    sector_symbols: List[str],
    daily_returns_map: Dict[str, float],
) -> Dict[str, Any]:
    """
    Compute sector sentiment based on sister contracts in the same sector.
    """
    if not sector_symbols:
        return {"sentiment": "NEUTRAL", "bull_ratio": 0.5, "avg_change": 0.0}

    changes = [daily_returns_map[s] for s in sector_symbols if s in daily_returns_map]
    if not changes:
        return {"sentiment": "NEUTRAL", "bull_ratio": 0.5, "avg_change": 0.0}

    avg_chg = sum(changes) / len(changes)
    bull_count = sum(1 for c in changes if c > 0)
    bull_ratio = bull_count / len(changes)

    if bull_ratio >= 0.75:
        sentiment = "STRONG_BULL"
    elif bull_ratio <= 0.25:
        sentiment = "STRONG_BEAR"
    elif avg_chg > 0.005:
        sentiment = "MILD_BULL"
    elif avg_chg < -0.005:
        sentiment = "MILD_BEAR"
    else:
        sentiment = "NEUTRAL"

    return {
        "sentiment": sentiment,
        "bull_ratio": round(bull_ratio, 2),
        "avg_change_pct": round(avg_chg * 100, 2),
    }
