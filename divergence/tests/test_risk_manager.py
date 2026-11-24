import pytest

from src.config import InstrumentMeta, RiskConfig
from src.divergence import DivergenceResult, PeakExtremum
from src.risk_manager import generate_trade_plan


def test_trade_plan_generation():
    p1 = PeakExtremum(10, "2026-11-20 10:00:00", 3500.0, 20.0, 10.0, 70.0, 1000.0, 10000.0)
    p2 = PeakExtremum(20, "2026-11-20 15:00:00", 3600.0, 15.0, 8.0, 64.0, 800.0, 9500.0)
    v_mid = PeakExtremum(15, "2026-11-20 13:00:00", 3400.0, 5.0, -2.0, 45.0, 600.0, 9800.0)

    div_res = DivergenceResult(
        has_divergence=True,
        direction="SHORT",
        p1=p1,
        p2=p2,
        v_mid=v_mid,
        macd_double_div=True,
        macd_cross_zero=True,
        rsi_div=True,
        volume_div=True,
        oi_div=True,
        score=0.9,
        resonance_level=3,
        details={},
    )

    meta = InstrumentMeta(symbol="RB0", name="螺纹钢", multiplier=10.0, tick_size=1.0)
    risk_cfg = RiskConfig(account_equity=100000.0, base_risk_ratio=0.01)

    plan = generate_trade_plan(
        div_result=div_res,
        current_price=3560.0,
        current_1h_atr=20.0,
        meta=meta,
        risk_config=risk_cfg,
        daily_tp1_382=3400.0,
        daily_tp2_667=3300.0,
    )

    assert plan is not None
    assert plan["direction"] == "SHORT"
    assert plan["entry_close_trigger"] < 3600.0
    assert plan["stop_loss"] > 3600.0
    assert plan["suggested_lots"] >= 1
    assert plan["tp1_382"] == 3400.0
    assert plan["rr_ratio_tp1"] > 1.0
