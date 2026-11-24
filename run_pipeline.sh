#!/usr/bin/env bash
# ==============================================================================
# Finalyst 量化流水线联合执行脚本 (fetchfu_db -> divergence)
# 用法:
#   ./run_pipeline.sh hourly   (抓取小时线增量 -> 运行小时级背离扫描出交易计划)
#   ./run_pipeline.sh daily    (抓取日线全量增量 -> 筛选日线候选池 -> 运行小时扫描)
# ==============================================================================

set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${BASE_DIR}/.venv/bin/python"
FETCH_DIR="${BASE_DIR}/fetchfu_db"
DIV_DIR="${BASE_DIR}/divergence"
LOG_DIR="${BASE_DIR}/logs"

mkdir -p "${LOG_DIR}"
NOW=$(date "+%Y-%m-%d %H:%M:%S")

MODE="${1:-hourly}"

echo "==================================================" >> "${LOG_DIR}/pipeline.log"
echo "[${NOW}] 启动量化流水线任务: 模式=${MODE}" >> "${LOG_DIR}/pipeline.log"

if [ "${MODE}" = "daily" ]; then
    # 1. 抓取日线全量增量 (日线 + 小时线)
    echo "[${NOW}] [Step 1/3] 开始同步日盘全量增量数据..." >> "${LOG_DIR}/pipeline.log"
    cd "${FETCH_DIR}" && "${PYTHON}" main.py --incremental --period all >> "${LOG_DIR}/pipeline.log" 2>&1

    # 2. 运行日线大级别 S/R 候选池初筛
    echo "[${NOW}] [Step 2/3] 开始运行日线关键S/R候选池筛选..." >> "${LOG_DIR}/pipeline.log"
    cd "${DIV_DIR}" && "${PYTHON}" main.py --daily >> "${LOG_DIR}/pipeline.log" 2>&1

    # 3. 运行小时线交易计划扫描与 CSV 导出
    echo "[${NOW}] [Step 3/3] 开始运行小时线背离扫描与计划生成..." >> "${LOG_DIR}/pipeline.log"
    cd "${DIV_DIR}" && "${PYTHON}" main.py --hourly >> "${LOG_DIR}/pipeline.log" 2>&1

else
    # 1. 抓取小时线增量
    echo "[${NOW}] [Step 1/2] 开始同步最新小时线增量数据..." >> "${LOG_DIR}/pipeline.log"
    cd "${FETCH_DIR}" && "${PYTHON}" main.py --incremental --period 60m >> "${LOG_DIR}/pipeline.log" 2>&1

    # 2. 运行小时线交易计划扫描与 CSV 导出
    echo "[${NOW}] [Step 2/2] 开始运行小时线背离扫描与计划生成..." >> "${LOG_DIR}/pipeline.log"
    cd "${DIV_DIR}" && "${PYTHON}" main.py --hourly >> "${LOG_DIR}/pipeline.log" 2>&1
fi

DONE_TIME=$(date "+%Y-%m-%d %H:%M:%S")
echo "[${DONE_TIME}] 流水线任务完成: 最新交易计划已更新至 divergence/reports/latest_hourly_signals.csv" >> "${LOG_DIR}/pipeline.log"
echo "==================================================" >> "${LOG_DIR}/pipeline.log"
