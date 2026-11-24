"""
Unit test for strategy evaluator & backtester.
"""

from src.config import load_config
from src.data_loader import DataLoader
from src.evaluator import StrategyEvaluator


def test_strategy_evaluator_runs():
    config = load_config()
    loader = DataLoader(config)
    evaluator = StrategyEvaluator(config, loader)

    # Run on single symbol with limited lookback
    trades = evaluator.evaluate_symbol("RB0", max_lookback_days=60)
    assert isinstance(trades, list)
    if trades:
        t = trades[0]
        assert "symbol" in t
        assert "pnl_r" in t
        assert "direction" in t
