# fetchfu_db 子代理（Subagent）协作与集成指南

本文档定义了 `fetchfu_db` 项目在多 Agent（Multi-Agent）开发体系中的角色职责，以及**未来下游新增项目（如量化回测、策略研发、实时监控等）对应的子代理**如何标准消费此数据库。

---

## 1. 当前子代理架构

| 子代理名称 | 角色类型 | 职责范围 | 操作权限 |
| :--- | :--- | :--- | :--- |
| **`fetchfu_developer`** | 数据采集与存储工程师 | 负责维护 `fetchfu_db` 自身代码：AkShare 接口维护、网络代理注入、SQLite WAL 存储、每日定时抓取调度及数据清洗入库。 | 读写权限（包含执行命令、测试与修改代码） |
| **下游业务子代理** *(后续新增)* | 量化策略 / 回测 / 看板等 | 负责基于 `data/futures_data.db` 开展业务计算。**严格以只读模式消费行情数据**，不直接修改底层数据表结构。 | 仅对数据库只读，拥有各自独立项目的代码读写权限 |

---

## 2. 下游子代理开发规范与数据消费指南

后续如果为下游新项目（如 `strategy_backtest`, `market_alert`）创建子代理，请遵循以下规范：

### 2.1 推荐方案：通过内置 SDK (`FuturesReader`) 查询

`fetchfu_db/src/reader.py` 提供了统一的只读连接封装，天然支持并发安全读取（基于 SQLite URI `?mode=ro` 模式，不抢占主采集进程的 WAL 写锁）：

```python
import sys
from pathlib import Path

# 添加 fetchfu_db 路径
FETCHFU_DIR = Path("/home/elvelyn/myapp/finalyst/fetchfu_db")
sys.path.insert(0, str(FETCHFU_DIR))

from src.reader import FuturesReader

# 1. 初始化只读连接 (自动从 config/config.yaml 读取数据库路径)
reader = FuturesReader()

# 2. 查询单个或多个品种日线 (返回 Pandas DataFrame)
df_daily = reader.get_daily_bars(
    symbol=["RB0", "CU0"],
    start_date="2026-01-01",
    end_date="2026-11-20"
)

# 3. 查询小时线 (60m)
df_hourly = reader.get_hourly_bars(
    symbol="RB0",
    start_time="2026-11-01 09:00:00",
    limit=500
)

# 4. 获取最新一根 Bar
latest_bar = reader.get_latest_bar("RB0", period="60m")

# 5. 获取库内覆盖的品种及有效时间范围
symbols = reader.get_symbols(period="daily")
start_dt, end_dt = reader.get_date_range("RB0", period="daily")
```

### 2.2 备选方案：直接使用 SQL 跨项目只读查询

下游子代理若使用原生 SQL 或其他语言环境，必须使用只读模式连接：

```python
import sqlite3

db_path = "/home/elvelyn/myapp/finalyst/fetchfu_db/data/futures_data.db"
# 强制使用只读模式 (mode=ro)，避免与抓取进程锁库
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

cursor = conn.cursor()
cursor.execute("""
    SELECT datetime, open, high, low, close, volume, open_interest 
    FROM hourly_bars 
    WHERE symbol = 'RB0' 
    ORDER BY datetime DESC 
    LIMIT 100
""")
rows = cursor.fetchall()
```

---

## 3. 数据表与时区规范约定

- **时区**：所有 `datetime` 列统一采用中国标准时间（`Asia/Shanghai`, UTC+8）。
- **`daily_bars`** 时间格式：`YYYY-MM-DD`（如 `2026-11-20`）。
- **`hourly_bars`** 时间格式：`YYYY-MM-DD HH:MM:SS`（如 `2026-11-20 15:00:00`）。
- **字段类型**：`open`, `high`, `low`, `close`, `volume`, `open_interest`, `turnover` 均为标准浮点数 (`REAL`)。
- **主键保证**：`(symbol, datetime)` 唯一联合主键，确保无重复 Bar。

---

## 4. 后续新增子代理推荐提示词模板

当后续需要为新项目生成子代理时，可以直接向主 Agent 描述，例如：
> “请创建一个名为 `backtest_agent` 的 subagent，在 `backtest/` 目录下开发双均线回测策略，从 `fetchfu_db` 的 SQLite 数据库中通过 `FuturesReader` 读取小时线数据。”
