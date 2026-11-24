"""
Strategy Evaluator & Backtester: Evaluates historical effectiveness of divergence signals.
Simulates trade execution (Entry trigger, SL, TP1, TP2) and computes key quantitative metrics:
Win Rate, Profit-to-Loss Ratio, Mathematical Expectancy, and Resonance Level Attribution.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from src.config import InstrumentMeta, StrategyConfig, load_config
from src.data_loader import DataLoader
from src.divergence import (
    check_bearish_divergence,
    check_bullish_divergence,
    find_local_extrema_1h,
)
from src.preprocessor import (
    compute_atr,
    compute_macd,
    compute_rsi,
    normalize_hourly_volume,
)
from src.risk_manager import generate_trade_plan

logger = logging.getLogger("divergence.evaluator")
console = Console()


class StrategyEvaluator:
    """
    Backtesting engine evaluating historical effectiveness across symbols.
    """

    def __init__(self, config: Optional[StrategyConfig] = None, loader: Optional[DataLoader] = None):
        self.config = config or load_config()
        self.loader = loader or DataLoader(self.config)

    def evaluate_symbol(
        self,
        symbol: str,
        max_lookback_days: int = 180,
        entry_timeout_bars: int = 12,
        max_hold_bars: int = 48,
    ) -> List[Dict[str, Any]]:
        """
        Backtest divergence strategy on a single symbol's historical 1H data.
        """
        sym = symbol.upper().strip()
        df = self.loader.get_hourly_bars(sym, limit_days=max_lookback_days)
        if len(df) < 120:
            return []

        meta = self.config.instruments.get(sym, InstrumentMeta(symbol=sym))

        # 1. Precalculate indicators on full series
        df = normalize_hourly_volume(df)
        df["atr"] = compute_atr(df, period=14)
        macd_df = compute_macd(
            df,
            fast=self.config.scanner.macd.fast_period,
            slow=self.config.scanner.macd.slow_period,
            signal=self.config.scanner.macd.signal_period,
        )
        df["dif"] = macd_df["dif"]
        df["dea"] = macd_df["dea"]
        df["macd_hist"] = macd_df["macd_hist"]
        df["rsi"] = compute_rsi(df, period=self.config.scanner.rsi.period)

        n_bars = len(df)
        window_size = self.config.scanner.lookback_bars
        trades: List[Dict[str, Any]] = []

        last_signal_idx = -999

        # Slide forward through time
        for t in range(window_size + 10, n_bars - max_hold_bars, 3):
            # Debounce: avoid duplicate signals within same wave
            if t - last_signal_idx < 10:
                continue

            sub_df = df.iloc[t - window_size : t + 1].reset_index(drop=True)
            cur_atr = float(sub_df["atr"].iloc[-1])
            if pd.isna(cur_atr) or cur_atr <= 0:
                continue

            cur_price = float(sub_df["close"].iloc[-1])
            peaks, troughs = find_local_extrema_1h(sub_df, window=3)

            bear_res = check_bearish_divergence(sub_df, peaks, troughs, cur_atr, self.config.scanner)
            bull_res = check_bullish_divergence(sub_df, peaks, troughs, cur_atr, self.config.scanner)
            div_res = bear_res or bull_res

            if not div_res or not div_res.has_divergence:
                continue

            # Generate plan
            plan = generate_trade_plan(
                div_result=div_res,
                current_price=cur_price,
                current_1h_atr=cur_atr,
                meta=meta,
                risk_config=self.config.risk,
            )
            if not plan:
                continue

            last_signal_idx = t
            sig_time = str(df["datetime"].iloc[t])
            direction = div_res.direction
            entry_price = plan["entry_touch_trigger"]
            sl_price = plan["stop_loss"]
            tp1_price = plan["tp1_382"]
            tp2_price = plan["tp2_667"]
            risk_dist = abs(entry_price - sl_price)

            if risk_dist <= 0:
                continue

            # Simulate forward execution
            future_df = df.iloc[t + 1 : min(t + 1 + max_hold_bars, n_bars)].reset_index(drop=True)
            trade_res = self._simulate_trade(
                direction=direction,
                entry_price=entry_price,
                sl_price=sl_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                risk_dist=risk_dist,
                future_df=future_df,
                entry_timeout=entry_timeout_bars,
            )

            if trade_res["status"] != "EXPIRED":
                trade_res.update(
                    {
                        "symbol": sym,
                        "name": meta.name,
                        "sector": meta.sector,
                        "signal_time": sig_time,
                        "direction": direction,
                        "resonance_level": div_res.resonance_level,
                        "score": div_res.score,
                        "entry_price": entry_price,
                        "sl_price": sl_price,
                        "tp1_price": tp1_price,
                        "tp2_price": tp2_price,
                        "risk_dist": risk_dist,
                    }
                )
                trades.append(trade_res)

        return trades

    def _simulate_trade(
        self,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        risk_dist: float,
        future_df: pd.DataFrame,
        entry_timeout: int,
    ) -> Dict[str, Any]:
        """
        Simulate trade lifecycle: entry trigger -> TP1 (60% scale-out + BE stop) -> TP2 / SL.
        Returns PnL in R-multiples (where 1.0 R = initial risk).
        """
        entered = False
        entry_bar = -1

        # Check for entry within timeout bars
        for idx in range(min(entry_timeout, len(future_df))):
            high = float(future_df["high"].iloc[idx])
            low = float(future_df["low"].iloc[idx])

            if direction == "SHORT":
                if high >= entry_price or low <= entry_price:
                    entered = True
                    entry_bar = idx
                    break
            else:  # LONG
                if low <= entry_price or high >= entry_price:
                    entered = True
                    entry_bar = idx
                    break

        if not entered:
            return {"status": "EXPIRED", "pnl_r": 0.0, "tp1_hit": False, "hold_bars": 0}

        # Track post-entry price movement
        tp1_hit = False
        active_sl = sl_price
        pnl_r = 0.0
        final_status = "HOLDING_TIMEOUT"
        hold_bars = 0

        for b in range(entry_bar + 1, len(future_df)):
            hold_bars += 1
            high = float(future_df["high"].iloc[b])
            low = float(future_df["low"].iloc[b])
            close = float(future_df["close"].iloc[b])

            if direction == "SHORT":
                # Check Stop Loss first (conservative)
                if high >= active_sl:
                    if tp1_hit:
                        # Breakeven stopped out on remaining 40%
                        # 60% locked at TP1, 40% at 0.0 R
                        final_status = "TP1_THEN_BE"
                    else:
                        pnl_r = -1.0
                        final_status = "STOP_LOSS"
                    break

                # Check TP2
                if low <= tp2_price:
                    r_tp1 = abs(entry_price - tp1_price) / risk_dist
                    r_tp2 = abs(entry_price - tp2_price) / risk_dist
                    pnl_r = 0.6 * r_tp1 + 0.4 * r_tp2
                    final_status = "TP1_AND_TP2"
                    break

                # Check TP1
                if not tp1_hit and low <= tp1_price:
                    tp1_hit = True
                    r_tp1 = abs(entry_price - tp1_price) / risk_dist
                    pnl_r += 0.6 * r_tp1
                    # Move remaining 40% stop loss to entry (breakeven)
                    active_sl = entry_price

            else:  # LONG
                if low <= active_sl:
                    if tp1_hit:
                        final_status = "TP1_THEN_BE"
                    else:
                        pnl_r = -1.0
                        final_status = "STOP_LOSS"
                    break

                if high >= tp2_price:
                    r_tp1 = abs(entry_price - tp1_price) / risk_dist
                    r_tp2 = abs(entry_price - tp2_price) / risk_dist
                    pnl_r = 0.6 * r_tp1 + 0.4 * r_tp2
                    final_status = "TP1_AND_TP2"
                    break

                if not tp1_hit and high >= tp1_price:
                    tp1_hit = True
                    r_tp1 = abs(entry_price - tp1_price) / risk_dist
                    pnl_r += 0.6 * r_tp1
                    active_sl = entry_price

        # If holding period ends without hitting SL or TP2
        if final_status == "HOLDING_TIMEOUT":
            last_close = float(future_df["close"].iloc[-1])
            unrealized_r = (
                (entry_price - last_close) / risk_dist
                if direction == "SHORT"
                else (last_close - entry_price) / risk_dist
            )
            if tp1_hit:
                pnl_r += 0.4 * max(-0.2, unrealized_r)
                final_status = "TP1_TIMEOUT"
            else:
                pnl_r = max(-1.0, unrealized_r)

        return {
            "status": final_status,
            "pnl_r": round(pnl_r, 3),
            "tp1_hit": tp1_hit,
            "hold_bars": hold_bars,
        }

    def run_all(
        self,
        symbols: Optional[List[str]] = None,
        max_lookback_days: int = 180,
    ) -> Dict[str, Any]:
        """
        Execute full evaluation across all target symbols and compute summary statistics.
        """
        all_symbols = symbols or self.loader.get_available_symbols()
        all_trades: List[Dict[str, Any]] = []

        console.print(f"🚀 开始策略历史有效性回测评估 (覆盖 {len(all_symbols)} 个品种)...")

        for sym in all_symbols:
            try:
                trades = self.evaluate_symbol(sym, max_lookback_days=max_lookback_days)
                all_trades.extend(trades)
            except Exception as e:
                logger.debug(f"Backtest error on {sym}: {e}")

        if not all_trades:
            console.print("[yellow]未回测到任何触发交易信号。[/yellow]")
            return {}

        df_trades = pd.DataFrame(all_trades)

        # 1. Overall Statistics
        total_trades = len(df_trades)
        wins = df_trades[df_trades["pnl_r"] > 0]
        losses = df_trades[df_trades["pnl_r"] < 0]
        win_rate = len(wins) / total_trades if total_trades else 0.0
        tp1_hit_rate = df_trades["tp1_hit"].mean()

        avg_win_r = wins["pnl_r"].mean() if len(wins) > 0 else 0.0
        avg_loss_r = abs(losses["pnl_r"].mean()) if len(losses) > 0 else 1.0
        profit_factor = (wins["pnl_r"].sum() / abs(losses["pnl_r"].sum())) if len(losses) > 0 and losses["pnl_r"].sum() != 0 else float("inf")
        expectancy_r = df_trades["pnl_r"].mean()

        # Cumulative PnL curve & Max Drawdown
        pnl_cumsum = df_trades["pnl_r"].cumsum()
        peak = pnl_cumsum.cummax()
        drawdown = peak - pnl_cumsum
        max_drawdown_r = float(drawdown.max())

        # 2. Breakdown by Resonance Level (L1, L2, L3)
        level_stats: Dict[int, Dict[str, Any]] = {}
        for lvl in [1, 2, 3]:
            sub = df_trades[df_trades["resonance_level"] == lvl]
            if len(sub) == 0:
                continue
            sub_wins = sub[sub["pnl_r"] > 0]
            sub_losses = sub[sub["pnl_r"] < 0]
            lvl_win_rate = len(sub_wins) / len(sub)
            lvl_tp1_rate = sub["tp1_hit"].mean()
            lvl_exp = sub["pnl_r"].mean()
            lvl_pf = (
                sub_wins["pnl_r"].sum() / abs(sub_losses["pnl_r"].sum())
                if len(sub_losses) > 0 and sub_losses["pnl_r"].sum() != 0
                else float("inf")
            )
            level_stats[lvl] = {
                "count": len(sub),
                "win_rate": lvl_win_rate,
                "tp1_hit_rate": lvl_tp1_rate,
                "expectancy_r": lvl_exp,
                "profit_factor": lvl_pf,
            }

        # 3. Print Rich Tables
        self._print_evaluation_results(
            total_trades=total_trades,
            win_rate=win_rate,
            tp1_hit_rate=tp1_hit_rate,
            avg_win_r=avg_win_r,
            avg_loss_r=avg_loss_r,
            profit_factor=profit_factor,
            expectancy_r=expectancy_r,
            max_drawdown_r=max_drawdown_r,
            level_stats=level_stats,
            df_trades=df_trades,
        )

        # 4. Save CSV archive
        report_dir = self.config.resolved_report_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        csv_file = report_dir / "backtest_trades.csv"
        df_trades.to_csv(csv_file, index=False, encoding="utf-8-sig")
        console.print(f"📊 回测明细记录已导出: [green]{csv_file}[/green]")

        return {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "tp1_hit_rate": tp1_hit_rate,
            "expectancy_r": expectancy_r,
            "profit_factor": profit_factor,
            "max_drawdown_r": max_drawdown_r,
            "level_stats": level_stats,
        }

    def _print_evaluation_results(
        self,
        total_trades: int,
        win_rate: float,
        tp1_hit_rate: float,
        avg_win_r: float,
        avg_loss_r: float,
        profit_factor: float,
        expectancy_r: float,
        max_drawdown_r: float,
        level_stats: Dict[int, Dict[str, Any]],
        df_trades: pd.DataFrame,
    ) -> None:
        console.print()
        console.print("=" * 65)
        console.print("📈 [bold cyan]Divergence 策略历史有效性量化回测评估总览[/bold cyan]")
        console.print("=" * 65)

        table = Table(title="策略核心数学期望与绩效看板", header_style="bold magenta")
        table.add_column("评估核心指标", style="bold yellow", width=25)
        table.add_column("统计数值", justify="right", style="bold white", width=18)
        table.add_column("基准达标参考", style="dim", width=22)

        table.add_row("触发有效样本交易总数", f"{total_trades} 笔", "大样本 (> 100)")
        table.add_row("整体胜率 (PnL > 0)", f"{win_rate * 100:.2f}%", "基准 >= 45.0%")
        table.add_row("第一目标 (TP1 38.2%) 触达率", f"{tp1_hit_rate * 100:.2f}%", "基准 >= 55.0%")
        table.add_row("平均盈利幅度 (Avg Win)", f"+{avg_win_r:.2f} R", "1.0R = 单笔初始风险")
        table.add_row("平均亏损幅度 (Avg Loss)", f"-{avg_loss_r:.2f} R", "保本机制截断亏损")
        table.add_row("盈亏比 (Profit Factor)", f"{profit_factor:.2f}", "优秀 >= 1.80")
        table.add_row("单笔数学期望 (Expectancy)", f"+{expectancy_r:.3f} R / 笔", "正期望 (> 0)")
        table.add_row("历史最大回撤 (Max Drawdown)", f"-{max_drawdown_r:.2f} R", "越小越稳健")
        console.print(table)

        # Level breakdown table
        lvl_table = Table(title="🌟 共振星级 (L1 / L2 / L3) 有效性分层检验看板", header_style="bold cyan")
        lvl_table.add_column("共振星级", justify="center", width=12)
        lvl_table.add_column("信号构成", width=26)
        lvl_table.add_column("触发笔数", justify="right", width=10)
        lvl_table.add_column("胜率", justify="right", style="bold green", width=12)
        lvl_table.add_column("TP1 触达率", justify="right", width=12)
        lvl_table.add_column("盈亏比", justify="right", width=10)
        lvl_table.add_column("单笔期望(R)", justify="right", style="bold yellow", width=14)

        desc_map = {
            1: "MACD 双背离",
            2: "MACD + (RSI 或 均量)",
            3: "MACD + RSI + OI 减仓共振",
        }
        stars_map = {1: "⭐ (L1)", 2: "⭐⭐ (L2)", 3: "⭐⭐⭐ (L3)"}

        for lvl in [1, 2, 3]:
            if lvl in level_stats:
                st = level_stats[lvl]
                lvl_table.add_row(
                    stars_map[lvl],
                    desc_map[lvl],
                    f"{st['count']}",
                    f"{st['win_rate'] * 100:.1f}%",
                    f"{st['tp1_hit_rate'] * 100:.1f}%",
                    f"{st['profit_factor']:.2f}",
                    f"+{st['expectancy_r']:.3f} R",
                )
        console.print(lvl_table)
        console.print()
