#!/usr/bin/env python3
"""
CLI Entrypoint for Chinese Futures Multi-Period Divergence & Retracement Screening System.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.daily_screener import DailyScreener
from src.data_loader import DataLoader
from src.hourly_scanner import HourlyScanner
from src.notifier import Notifier, console

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("divergence.main")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="divergence",
        description="Chinese Futures Multi-Period Divergence & Retracement Screener",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --daily
  python main.py --hourly
  python main.py --hourly --symbols RB0,CU0,IF0
  python main.py --status
        """,
    )

    parser.add_argument(
        "--daily",
        action="store_true",
        help="Run Daily Screener to identify key S/R zone candidates and Fibonacci space.",
    )
    parser.add_argument(
        "--hourly",
        action="store_true",
        help="Run Hourly Scanner to detect 1H divergences, 23.6%% triggers, and trade plans.",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated list of symbols (e.g. 'RB0,CU0,IF0').",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display summary statistics of database symbols and recent reports.",
    )
    parser.add_argument(
        "--eval",
        "--backtest",
        dest="eval",
        action="store_true",
        help="Run Historical Evaluator & Backtester to evaluate win rates and expectancy across symbols.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=180,
        help="Lookback days for historical backtest evaluation (default: 180 days).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Custom path to config.yaml configuration file.",
    )

    return parser.parse_args()


def handle_status(config, loader: DataLoader) -> int:
    symbols = loader.get_available_symbols()
    report_files = sorted(config.resolved_report_dir.glob("*.json"), reverse=True)

    console.print()
    console.print("=" * 50)
    console.print(f"📊 [bold cyan]Divergence 策略系统状态看板[/bold cyan]")
    console.print(f"数据库路径:       {config.resolved_db_path}")
    console.print(f"库内可用主力品种: {len(symbols)} 个")
    if symbols:
        console.print(f"品种样本:         {', '.join(symbols[:15])} ...")
    console.print(f"报告归档目录:     {config.resolved_report_dir}")
    console.print(f"已生成报告总数:   {len(report_files)} 个")
    if report_files:
        console.print(f"最新报告:         {report_files[0].name}")
    console.print("=" * 50)
    console.print()
    return 0


def main() -> int:
    args = parse_arguments()
    config = load_config(args.config)
    loader = DataLoader(config)
    notifier = Notifier(config)

    # 1. Status Command
    if args.status:
        return handle_status(config, loader)

    target_symbols = None
    if args.symbols:
        target_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # 2. Historical Evaluation / Backtest Command
    if args.eval:
        from src.evaluator import StrategyEvaluator
        evaluator = StrategyEvaluator(config, loader)
        evaluator.run_all(symbols=target_symbols, max_lookback_days=args.days)
        return 0

    # 2. Daily Screener Command
    if args.daily:
        logger.info("Executing Daily Screener...")
        screener = DailyScreener(config, loader)
        candidates = screener.run_all(symbols=target_symbols)
        notifier.print_daily_candidates(candidates)
        report_path = notifier.archive_json_report("daily_candidates", candidates)
        csv_path = notifier.export_csv_report("daily_candidates", candidates)
        if report_path:
            console.print(f"💾 日线候选池 JSON 已归档: [green]{report_path}[/green]")
        if csv_path:
            console.print(f"📊 日线候选池 CSV  已导出: [green]{csv_path}[/green] (及 latest_daily_candidates.csv)")
        return 0

    # 3. Hourly Scanner Command
    if args.hourly:
        logger.info("Executing Hourly Scanner...")
        screener = DailyScreener(config, loader)
        daily_candidates = screener.run_all(symbols=target_symbols)
        daily_map = {c["symbol"]: c for c in daily_candidates}

        scanner = HourlyScanner(config, loader)
        # If target symbols explicitly specified, scan those; otherwise scan all or daily candidates
        scan_symbols = target_symbols if target_symbols else list(daily_map.keys())
        if not scan_symbols:
            logger.info("No daily candidates found. Scanning all available symbols...")
            scan_symbols = loader.get_available_symbols()

        signals = scanner.run_all(symbols=scan_symbols, daily_candidates_map=daily_map)
        notifier.print_hourly_signals(signals)
        report_path = notifier.archive_json_report("hourly_signals", signals)
        csv_path = notifier.export_csv_report("hourly_signals", signals)
        if report_path:
            console.print(f"💾 小时线交易计划 JSON 已归档: [green]{report_path}[/green]")
        if csv_path:
            console.print(f"📊 小时线交易计划 CSV  已导出: [green]{csv_path}[/green] (及 latest_hourly_signals.csv)")
        return 0

    # Default: Show help
    print("未指定执行操作。请使用 --daily, --hourly, --symbols 或 --status。")
    print("详情请运行: python main.py --help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
