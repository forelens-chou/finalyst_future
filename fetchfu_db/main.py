#!/usr/bin/env python3
"""
CLI entrypoint for domestic Chinese futures market data collection (fetchfu_db).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config, setup_logger
from src.network import check_proxy
from src.runner import SyncRunner
from src.storage import StorageManager


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fetchfu_db",
        description="Domestic Chinese Futures Market Data Collector (AkShare -> SQLite)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --all
  python main.py --period 1d
  python main.py --period 60m
  python main.py --symbols RB0,CU0,IF0
  python main.py --all --days 365
  python main.py --check-proxy
  python main.py --status
        """,
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch both daily (1d) and hourly (60m) bar data for target symbols.",
    )
    parser.add_argument(
        "-i",
        "--incremental",
        action="store_true",
        help="Incremental update mode: only fetch recent missing bars (3-5 days) for symbols already in DB.",
    )
    parser.add_argument(
        "-r",
        "--resume",
        action="store_true",
        help="Resume interrupted sync: skip symbols that already have complete data in database.",
    )
    parser.add_argument(
        "--period",
        type=str,
        choices=["1d", "60m", "all"],
        default=None,
        help="Data frequency period to fetch ('1d', '60m', or 'all').",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated list of symbols to fetch (e.g. 'RB0,CU0,IF0').",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Number of historical days to sync (overrides config.fetch.history_days).",
    )
    parser.add_argument(
        "--check-proxy",
        action="store_true",
        help="Verify proxy configuration, external IP, and Sina endpoint connectivity.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display summary statistics of existing SQLite database.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Custom path to config.yaml configuration file.",
    )

    return parser.parse_args()


def handle_check_proxy(config) -> int:
    print("\n--- 正在探测网络与代理配置 ---")
    res = check_proxy(config)
    print(f"代理启用状态:   {'已启用' if res['proxy_enabled'] else '未启用 (直连)'}")
    if res["proxy_enabled"]:
        print(f"HTTP 代理:      {res['proxy_urls'].get('http') or '无'}")
        print(f"HTTPS 代理:     {res['proxy_urls'].get('https') or '无'}")
    print(f"外网出口 IP:    {res['ip'] or '未能获取'}")
    latency_str = f"{res['latency_ms']} ms" if res['latency_ms'] is not None else "N/A"
    print(f"外网延迟:       {latency_str}")
    print(f"新浪期货连通性: {'成功' if res['sina_connectivity'] else '失败'}")
    if res["sina_latency_ms"] is not None:
        print(f"新浪接口延迟:   {res['sina_latency_ms']} ms")
    print(f"状态详情:       {res['message']}")
    print("--------------------------------\n")
    return 0 if res["success"] else 1


def handle_status(config) -> int:
    storage = StorageManager.from_config(config)
    stats = storage.get_database_status()

    daily = stats["daily_bars"]
    hourly = stats["hourly_bars"]

    print("\n========== SQLite 数据库统计状态 ==========")
    print(f"数据库路径:       {stats['db_path']}")
    print(f"数据库文件大小:   {stats['file_size_mb']} MB")
    print("\n[日线数据 (daily_bars)]")
    print(f"  总记录条数:     {daily['total_rows']:,}")
    print(f"  覆盖品种数:     {daily['symbols_count']}")
    print(f"  时间跨度:       {daily['min_date'] or '无'} ~ {daily['max_date'] or '无'}")
    if daily["symbols"]:
        sym_sample = ", ".join(daily["symbols"][:15])
        if len(daily["symbols"]) > 15:
            sym_sample += f" ... 等共 {len(daily['symbols'])} 个"
        print(f"  品种清单:       {sym_sample}")

    print("\n[小时线数据 (hourly_bars)]")
    print(f"  总记录条数:     {hourly['total_rows']:,}")
    print(f"  覆盖品种数:     {hourly['symbols_count']}")
    print(f"  时间跨度:       {hourly['min_datetime'] or '无'} ~ {hourly['max_datetime'] or '无'}")
    if hourly["symbols"]:
        sym_sample = ", ".join(hourly["symbols"][:15])
        if len(hourly["symbols"]) > 15:
            sym_sample += f" ... 等共 {len(hourly['symbols'])} 个"
        print(f"  品种清单:       {sym_sample}")
    print("==========================================\n")
    return 0


def main() -> int:
    args = parse_arguments()
    config = load_config(args.config)
    setup_logger(config)

    # 1. Check Proxy
    if args.check_proxy:
        return handle_check_proxy(config)

    # 2. Database Status
    if args.status:
        return handle_status(config)

    # 3. Synchronize bars
    period = "all"
    if args.period:
        period = args.period
    elif args.all or args.incremental or args.resume:
        period = "all"
    elif args.symbols:
        # If symbols specified without explicit period, default to all
        period = "all"
    else:
        # No action specified, print help and exit
        print("未指定执行操作。请使用 --incremental, --resume, --all, --period, --symbols, --status 或 --check-proxy。")
        print("详情请运行: python main.py --help")
        return 0

    runner = SyncRunner(config)
    summary = runner.run(
        symbols=args.symbols,
        period=period,
        history_days=args.days,
        incremental=args.incremental,
        resume=args.resume,
    )

    # Return non-zero if all symbols failed when there were targets
    if summary["total_symbols"] > 0 and summary["successful_symbols"] == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
