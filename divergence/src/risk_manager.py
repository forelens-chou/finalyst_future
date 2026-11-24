"""
Risk management, position sizing, Fibonacci trigger, and trade plan generation.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional
from src.config import InstrumentMeta, RiskConfig
from src.divergence import DivergenceResult


def generate_trade_plan(
    div_result: DivergenceResult,
    current_price: float,
    current_1h_atr: float,
    meta: InstrumentMeta,
    risk_config: RiskConfig,
    daily_tp1_382: Optional[float] = None,
    daily_tp2_667: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Generate actionable trade plan with 23.6% trigger, SL, position size, and TP1/TP2.
    """
    direction = div_result.direction
    tick_size = meta.tick_size
    multiplier = meta.multiplier
    buffer_val = risk_config.buffer_ticks * tick_size
    atr_buffer = risk_config.stop_loss_atr_multiplier * current_1h_atr
    sl_extra = max(2 * tick_size, atr_buffer)

    p2_price = div_result.p2.price
    vmid_price = div_result.v_mid.price
    span = abs(p2_price - vmid_price)

    if direction == "SHORT":
        # Microstructure anchor: from p2 (high) down to v_mid (low)
        touch_trigger = p2_price - 0.236 * span
        close_trigger = round(touch_trigger - buffer_val, 2)
        touch_trigger = round(touch_trigger, 2)
        stop_loss = round(p2_price + sl_extra, 2)

        # Ensure stop loss is above trigger
        risk_per_point = stop_loss - close_trigger
        if risk_per_point <= 0:
            return None

        # Take Profit targets
        if daily_tp1_382 and daily_tp1_382 < close_trigger:
            tp1 = daily_tp1_382
        else:
            tp1 = round(close_trigger - 0.382 * span * 2, 2)

        if daily_tp2_667 and daily_tp2_667 < tp1:
            tp2 = daily_tp2_667
        else:
            tp2 = round(close_trigger - 0.667 * span * 2, 2)

    else:  # LONG
        touch_trigger = p2_price + 0.236 * span
        close_trigger = round(touch_trigger + buffer_val, 2)
        touch_trigger = round(touch_trigger, 2)
        stop_loss = round(p2_price - sl_extra, 2)

        risk_per_point = close_trigger - stop_loss
        if risk_per_point <= 0:
            return None

        if daily_tp1_382 and daily_tp1_382 > close_trigger:
            tp1 = daily_tp1_382
        else:
            tp1 = round(close_trigger + 0.382 * span * 2, 2)

        if daily_tp2_667 and daily_tp2_667 > tp1:
            tp2 = daily_tp2_667
        else:
            tp2 = round(close_trigger + 0.667 * span * 2, 2)

    risk_per_lot_cny = round(risk_per_point * multiplier, 2)
    if risk_per_lot_cny <= 0:
        return None

    # Resonance weight: level 1 -> 0.5, level 2 -> 0.75, level 3 -> 1.0
    weight_key = f"level_{div_result.resonance_level}"
    weight = risk_config.resonance_weights.get(weight_key, 0.50)

    # Lots sizing
    risk_budget = risk_config.account_equity * risk_config.base_risk_ratio * weight
    suggested_lots = int(math.floor(risk_budget / risk_per_lot_cny))

    # Calculate R:R
    gain_to_tp1 = abs(close_trigger - tp1)
    rr_ratio = round(gain_to_tp1 / risk_per_point, 2) if risk_per_point > 0 else 0.0

    return {
        "direction": direction,
        "entry_mode": risk_config.entry_mode,
        "entry_close_trigger": close_trigger,
        "entry_touch_trigger": touch_trigger,
        "current_price": round(current_price, 2),
        "stop_loss": stop_loss,
        "risk_ticks": round(risk_per_point / tick_size, 1),
        "risk_per_lot_cny": risk_per_lot_cny,
        "resonance_level": div_result.resonance_level,
        "resonance_weight": weight,
        "suggested_lots": suggested_lots,
        "valid_lot_size": suggested_lots >= 1,
        "tp1_382": tp1,
        "tp1_close_percent": risk_config.tp1_close_percent,
        "tp2_667": tp2,
        "rr_ratio_tp1": rr_ratio,
    }
