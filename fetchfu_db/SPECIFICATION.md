# 中国国内期货行情数据采集系统 - 开发规格说明书 (SPECIFICATION)

## 1. 系统架构与目录结构

### 1.1 目录组织
```text
fetchfu_db/
├── config/
│   ├── config.yaml            # 系统主配置文件（代理、标的清单、数据库路径、流控参数）
│   └── symbols.default.json   # 默认五大交易所主力连续合约映射清单
├── data/
│   └── futures_data.db        # SQLite 数据库存储文件（自动生成）
├── logs/
│   └── fetchfu.log            # 轮转运行日志文件（自动生成）
├── src/
│   ├── __init__.py
│   ├── config.py              # 配置解析与模型校验（基于 dataclasses / pydantic）
│   ├── network.py             # 代理注入、网络连通性探测与重试装饰器
│   ├── symbols.py             # 品种代码获取与标准化解析模块
│   ├── fetcher.py             # AkShare 行情数据抓取与数据结构清洗转换
│   ├── storage.py             # SQLite 连接池、表初始化、批量 UPSERT 存盘
│   └── runner.py              # 批量抓取引擎、并发/串行流控与批次统计
├── tests/
│   ├── test_proxy.py          # 代理连通性测试
│   └── test_fetcher.py        # 行情抓取与转换测试
├── main.py                    # CLI 命令行入口
├── requirements.txt           # Python 核心依赖列表
├── crontab.sample             # 生产级 Crontab 定时配置参考
├── REQUIREMENTS.md            # 开发需求说明书
└── SPECIFICATION.md           # 开发规格说明书（本文档）
```

---

## 2. 数据库设计规格 (Database Design)

### 2.1 存储技术选型
- **引擎**：SQLite 3
- **连接选项**：
  - 开启 `PRAGMA journal_mode = WAL;`（写前日志模式，大幅提升并发读写吞吐，降低锁库风险）。
  - 开启 `PRAGMA synchronous = NORMAL;`（在保证安全的前提下最大化写入性能）。
  - 开启 `PRAGMA busy_timeout = 5000;`（超时等待 5 秒防止锁库冲突）。

### 2.2 表结构定义 (DDL)

#### 2.2.1 日线数据表 `daily_bars`
```sql
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol          TEXT NOT NULL,          -- 品种/合约代码（如 'RB0', 'CU0', 'IF0'）
    datetime        TEXT NOT NULL,          -- 交易日期，格式：YYYY-MM-DD (Asia/Shanghai)
    open            REAL NOT NULL,          -- 开盘价
    high            REAL NOT NULL,          -- 最高价
    low             REAL NOT NULL,          -- 最低价
    close           REAL NOT NULL,          -- 收盘价
    volume          REAL DEFAULT 0,         -- 成交量（手）
    open_interest   REAL DEFAULT 0,         -- 持仓量 / 未平仓合约数
    turnover        REAL DEFAULT 0,         -- 成交金额（元）
    updated_at      TEXT NOT NULL,          -- 记录写入/更新时间 (YYYY-MM-DD HH:MM:SS)
    PRIMARY KEY (symbol, datetime)
);

CREATE INDEX IF NOT EXISTS idx_daily_symbol_dt ON daily_bars (symbol, datetime DESC);
```

#### 2.2.2 小时线数据表 `hourly_bars`
```sql
CREATE TABLE IF NOT EXISTS hourly_bars (
    symbol          TEXT NOT NULL,          -- 品种/合约代码（如 'RB0', 'CU0'）
    datetime        TEXT NOT NULL,          -- K线时间戳，格式：YYYY-MM-DD HH:MM:SS (Asia/Shanghai)
    open            REAL NOT NULL,          -- 周期开盘价
    high            REAL NOT NULL,          -- 周期最高价
    low             REAL NOT NULL,          -- 周期最低价
    close           REAL NOT NULL,          -- 周期收盘价
    volume          REAL DEFAULT 0,         -- 周期成交量
    open_interest   REAL DEFAULT 0,         -- 周期持仓量
    turnover        REAL DEFAULT 0,         -- 周期成交额
    updated_at      TEXT NOT NULL,          -- 记录写入/更新时间
    PRIMARY KEY (symbol, datetime)
);

CREATE INDEX IF NOT EXISTS idx_hourly_symbol_dt ON hourly_bars (symbol, datetime DESC);
```

### 2.3 写入策略（幂等 UPSERT）
通过批量事务执行：
```sql
INSERT INTO daily_bars (
    symbol, datetime, open, high, low, close, volume, open_interest, turnover, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, datetime) DO UPDATE SET
    open = excluded.open,
    high = excluded.high,
    low = excluded.low,
    close = excluded.close,
    volume = excluded.volume,
    open_interest = excluded.open_interest,
    turnover = excluded.turnover,
    updated_at = excluded.updated_at;
```

---

## 3. 配置文件规格 (`config/config.yaml`)

```yaml
# fetchfu_db 系统配置文件

# 1. 代理网络配置（境外服务器必填，国内服务器可留空）
network:
  proxy:
    enabled: true
    # 支持 http://、https:// 或 socks5h:// 协议
    http_url: "socks5h://127.0.0.1:10808"   # 示例：通过本地转发或国内中继节点
    https_url: "socks5h://127.0.0.1:10808"
  timeout_seconds: 15
  retry_count: 3
  retry_backoff_factor: 1.5

# 2. 数据库配置
database:
  db_path: "data/futures_data.db"
  wal_mode: true

# 3. 抓取参数配置
fetch:
  history_days: 365                  # 同步近 1 年数据（可调整为 365~730 天）
  hourly_period: "60"                # 60分钟线
  pacing_min_seconds: 0.6            # 请求最小随机休眠秒数
  pacing_max_seconds: 1.5            # 请求最大随机休眠秒数

# 4. 品种范围配置
symbols:
  use_default_continuous: true       # 是否启用系统内置五大交易所主力连续合约
  custom_symbols:                    # 自定义追加的特定合约或品种
    - "RB0"                          # 螺纹钢连续
    - "HC0"                          # 热卷连续
    - "CU0"                          # 铜连续
    - "AU0"                          # 黄金连续
    - "SC0"                          # 原油连续
    - "IF0"                          # 沪深300股指连续
    - "LC0"                          # 碳酸锂连续
  exclude_symbols: []                # 排除的品种列表

# 5. 日志配置
logging:
  log_level: "INFO"
  log_file: "logs/fetchfu.log"
  max_bytes: 10485760               # 10MB
  backup_count: 5
```

---

## 4. 关键核心模块技术规格

### 4.1 网络代理穿透模块 (`src/network.py`)
- **注入机制**：
  由于 `AkShare` 内部大部分使用 `requests` 或 `urllib` 请求上游，系统在启动时通过读取 `network.proxy` 配置：
  1. 动态挂载环境变量 `os.environ["HTTP_PROXY"]`、`os.environ["HTTPS_PROXY"]` 和 `os.environ["ALL_PROXY"]`。
  2. 若使用 SOCKS5，通过 `urllib3.contrib.socks` 或 `requests[socks]` 提供 DNS 远端解析支持（推荐使用 `socks5h://` 协议，避免在境外服务器本地解析国内金融域名遭 DNS 污染或污染延迟）。
- **代理自检**：
  启动提供 `--check-proxy`，请求测试接口（如 `http://httpbin.org/ip` 或国内轻量公共查询服务）验证出口 IP 及延迟。

### 4.2 数据抓取与清洗适配器 (`src/fetcher.py`)
- **AkShare 接口调用映射**：
  1. **日线数据**：
     - 调用 `ak.futures_zh_daily_sina(symbol=symbol)`。
     - 若接口返回包含 `date`, `open`, `high`, `low`, `close`, `volume`, `hold`（持仓量）等列，清洗为统一列名。
     - 按 `config.history_days` 进行时间切片截取。
  2. **小时线数据（60m）**：
     - 调用 `ak.futures_zh_minute_sina(symbol=symbol, period='60')`。
     - 原始时间字段形如 `2026-11-20 15:00:00`，转换为标准字符串。
     - 截取所需的时间跨度。
- **数据清洗规则**：
  - 缺失值处理：若关键 OHLC 存在 NaN，剔除异常行；若成交量/持仓量为空，默认填补 0.0。
  - 数值类型转换：确保 open/high/low/close/volume/open_interest 统一转为 `float`。

### 4.3 存储与事务引擎 (`src/storage.py`)
- 使用 Python 内置 `sqlite3`。
- 采用 `executemany` 结合上下文管理器 `with sqlite3.connect(...) as conn:`，单品种单次全量批量写入为一个完整原子事务，避免长时间占用写锁。

### 4.4 流控与异常重试引擎 (`src/runner.py`)
- 抓取主循环：
  ```python
  for symbol in symbol_list:
      try:
          # 1. 抓取日线
          # 2. 抓取小时线
          # 3. 写入数据库
          # 4. 随机等待 (pacing_min ~ pacing_max)
      except Exception as e:
          # 记录失败并放入 failed_symbols 列表
          continue
  ```
- 汇总统计：最后打印同步看板：
  ```text
  ========== 同步汇总看板 ==========
  总目标品种数: 72
  成功品种数:   70
  失败品种数:   2 (SC0, LC0)
  新增/更新记录: 日线 25,480 条, 小时线 58,240 条
  总耗时:       128.5 秒
  ==================================
  ```

---

## 5. CLI 命令行接口规范 (`main.py`)

支持标准参数调用：

```bash
# 1. 全量执行（同步日线 + 小时线，覆盖所有配置品种）
python main.py --all

# 2. 仅同步日线
python main.py --period 1d

# 3. 仅同步小时线
python main.py --period 60m

# 4. 仅同步指定品种（支持逗号分隔）
python main.py --symbols RB0,CU0,IF0

# 5. 指定回溯天数覆盖配置
python main.py --all --days 365

# 6. 检查网络与代理连通性
python main.py --check-proxy

# 7. 查看当前数据库内品种统计
python main.py --status
```

---

## 6. 生产 Crontab 调度规划 (`crontab.sample`)

中国国内期货交易时段较为特殊，由多个离散的交易小节构成：
- **日盘**：09:00 - 10:15（第1小节，10:15形成第1根60m Bar）, 10:30 - 11:30（第2小节，11:30形成第2根Bar）, 13:30 - 15:00（14:30形成第3根Bar，15:00收盘）
- **夜盘**：21:00 - 23:00（大部分品种），部分黑色/有色延至 01:00，贵金属及原油延至次日 02:30

### 6.1 调度模式设计对比

1. **模式一：日内小时级准实时增量入库（推荐：日内策略、监控分析）**
   - 在每个交易小节完成后立即触发（如 10:16、11:31、14:31、15:15、22:01、23:01、01:01、02:35）。
   - 依赖系统的 `--incremental` 增量模式，单次全市场更新耗时不到 30 秒，实时沉淀最新完成的 60m Bar。
2. **模式二：盘后批量归档（中低频回测、日终风控）**
   - 每日仅在日盘收盘（15:15）与夜盘收盘（02:45）执行 2 次。
   - 适合仅做隔日/周级别策略研究的场景，最小化外部接口请求调用频次。

### 6.2 Crontab 配置参考 (Asia/Shanghai 时区)

```bash
# === 模式一：日内小时小节完成后准实时增量入库 ===
# 1. 早盘各小节完成后增量拉取小时线 (10:16, 11:31)
16 10,31 11 * * 1-5 cd /home/elvelyn/myapp/finalyst/fetchfu_db && /home/elvelyn/myapp/finalyst/.venv/bin/python main.py --incremental --period 60m >> logs/cron.log 2>&1

# 2. 下午盘 14:31 增量拉取小时线
31 14 * * 1-5 cd /home/elvelyn/myapp/finalyst/fetchfu_db && /home/elvelyn/myapp/finalyst/.venv/bin/python main.py --incremental --period 60m >> logs/cron.log 2>&1

# 3. 日盘 15:15 最终全天收盘（同步最终小时线 + 全天日线）
15 15 * * 1-5 cd /home/elvelyn/myapp/finalyst/fetchfu_db && /home/elvelyn/myapp/finalyst/.venv/bin/python main.py --incremental --period all >> logs/cron.log 2>&1

# 4. 夜盘前半段各时段增量拉取小时线 (22:01, 23:01)
01 22,23 * * 1-5 cd /home/elvelyn/myapp/finalyst/fetchfu_db && /home/elvelyn/myapp/finalyst/.venv/bin/python main.py --incremental --period 60m >> logs/cron.log 2>&1

# 5. 深夜/次日凌晨各时段增量拉取小时线 (00:01, 01:01, 02:35 周二至周六)
01 00,01 * * 2-6 cd /home/elvelyn/myapp/finalyst/fetchfu_db && /home/elvelyn/myapp/finalyst/.venv/bin/python main.py --incremental --period 60m >> logs/cron.log 2>&1
35 02 * * 2-6 cd /home/elvelyn/myapp/finalyst/fetchfu_db && /home/elvelyn/myapp/finalyst/.venv/bin/python main.py --incremental --period 60m >> logs/cron.log 2>&1
```

---

## 7. 部署与验收步骤

1. **环境准备与依赖安装**：
   ```bash
   cd /home/elvelyn/myapp/finalyst/fetchfu_db
   ../.venv/bin/pip install -r requirements.txt
   ```
2. **配置测试**：
   在 `config/config.yaml` 中配置好可用代理节点，运行：
   ```bash
   ../.venv/bin/python main.py --check-proxy
   ```
3. **单品种试跑**：
   ```bash
   ../.venv/bin/python main.py --symbols RB0 --days 30
   ../.venv/bin/python main.py --status
   ```
4. **全量首次初始化**：
   ```bash
   ../.venv/bin/python main.py --all
   ```
5. **部署至 Crontab** 自动化持续运行。
