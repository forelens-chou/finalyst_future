# 中国期货多周期背离与回撤量化筛选系统 - 技术规格说明书 (SPECIFICATION)

## 1. 系统概述与整体架构

### 1.1 系统定位
本项目（`divergence`）定位为基于现有 `fetchfu_db` SQLite 数据库（日线 1D 与小时线 1H）的**双模式多周期量化筛选与交易计划生成系统**。
系统通过量化算法替代传统主观画线，解决国内期货 1H K 线交易时长不均造成的虚假成交量背离、换月跳空干扰，并结合持仓量（Open Interest）背离、ADX 单边趋势状态机与板块联动共振，输出具有严格数学定义的高盈亏比交易计划。

### 1.2 系统架构分层
```text
┌─────────────────────────────────────────────────────────────┐
│                       CLI 入口 (main.py)                    │
│   --daily (日盘候选池) | --hourly (小时信号) | --symbol 等   │
└──────────────────────────────┬──────────────────────────────┘
                               │
       ┌───────────────────────┴───────────────────────┐
       ▼                                               ▼
┌──────────────────────────────┐        ┌──────────────────────────────┐
│  Daily Screener (日线筛选器)  │        │  Hourly Scanner (小时扫描器)  │
│  - 3.0*ATR ZigZag S/R 热区   │        │  - 局部极值定位 P1/P2/Vmid    │
│  - 20/60 日唐奇安通道边界    │        │  - MACD双背离 / RSI背离      │
│  - 空间测算 (38.2% >= 2*ATR) │        │  - 持仓量 (OI) 减仓背离      │
│  - 1D ADX 强单边过滤拦截     │        │  - 23.6% 微结构触发 & 止损    │
└──────────────┬───────────────┘        └──────────────┬───────────────┘
               │                                       │
               └───────────────────┬───────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────┐
│             Risk & Execution Engine (风控与计划生成)         │
│  - 以损定仓 (固定风险 R% × 共振权重 W)                       │
│  - 动态分级止盈 (TP1 38.2% 平仓50%+保本 / TP2 66.7% / 移动止盈)│
│  - 涨跌停保护、高成本过滤与收盘前 30 分钟熔断                 │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│               Notification & Reporting Bus                  │
│  - 终端 Rich 彩色格式化表格看板                              │
│  - 本地 reports/ 目录 JSON 结构化归档                        │
│  - 预留抽象接口：EmailNotifier (邮件) / WebhookNotifier     │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│          Data Layer (只读接入 fetchfu_db SQLite)            │
│  - FuturesReader (mode=ro, Asia/Shanghai, 70+ 主力合约)     │
│  - 1H 交易时长归一化 (norm_vol = vol / active_mins * 60)    │
│  - 品种元数据 (instrument_config.json: tick_size, 乘数等)   │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 目录布局与文件规划

```text
divergence/
├── config/
│   ├── config.yaml              # 策略核心阈值、权重评分、风控参数与输出配置
│   └── instrument_config.json   # 70+ 期货品种静态属性 (乘数、最小变动价位、板块归属、手续费率)
├── reports/                     # 扫描结果及交易计划 JSON 结构化归档目录 (自动生成)
├── src/
│   ├── __init__.py              # 包导出
│   ├── config.py                # 强类型配置解析 (StrategyConfig, RiskConfig 等)
│   ├── data_loader.py           # 数据库接入适配器 (封装 FuturesReader 只读调用)
│   ├── preprocessor.py          # 特征计算、时区处理、1H 成交量时长归一化 (norm_vol)
│   ├── sr_zone.py               # ATR-ZigZag 支撑阻力区识别、邻近聚类合并与空间测算
│   ├── divergence.py            # 核心背离检测 (MACD快线+柱体双背离、RSI背离、OI持仓背离)
│   ├── filters.py               # 过滤器 (ADX单边趋势状态机、板块联动情绪、收盘熔断)
│   ├── risk_manager.py          # 23.6% 触发价、止损点、以损定仓与 TP1/TP2 交易计划生成
│   ├── daily_screener.py        # 日线级候选池筛选器引擎
│   ├── hourly_scanner.py        # 小时线级信号扫描与触发引擎
│   └── notifier.py              # 终端 Rich 看板渲染、JSON 归档与通知总线接口
├── tests/                       # 单元与集成测试集
│   ├── conftest.py
│   ├── test_preprocessor.py     # 归一化与特征计算测试
│   ├── test_sr_zone.py          # ZigZag 与 S/R 区合并测试
│   ├── test_divergence.py       # 背离识别算法测试
│   ├── test_risk_manager.py     # 斐波那契与仓位计算测试
│   └── test_screener.py         # 筛选器集成与 CLI 解析测试
├── main.py                      # 统一 CLI 执行入口
├── requirements.txt             # 项目依赖清单 (numpy, pandas, rich, pyyaml, pytest)
├── spec.md                      # 原始业务需求文档 (PRD)
└── specification.md             # 本技术规格说明书
```

---

## 3. 数据层与预处理算法规格

### 3.1 数据源契约
数据通过 `fetchfu_db` 提供的只读 SDK `FuturesReader` 加载：
```python
from fetchfu_db.src.reader import FuturesReader
reader = FuturesReader()
df_daily = reader.get_daily_bars(symbol, start_date=...)
df_hourly = reader.get_hourly_bars(symbol, start_time=...)
```
数据表字段约定：`symbol`, `datetime` (Asia/Shanghai), `open`, `high`, `low`, `close`, `volume`, `open_interest`, `turnover`。

### 3.2 1H K 线成交量归一化 (`norm_vol`)
国内期货各交易小节实际交易分钟数不一致（如 14:30~15:00 仅 30 分钟，早盘 10:30~11:30 为 60 分钟，早盘 09:00~10:15 为 75 分钟并于 10:15 成线）。未经归一化的原始成交量会产生伪“缩量背离”。

**有效分钟数映射算法**：
```python
def get_bar_active_minutes(datetime_str: str, exchange: str) -> int:
    time_part = datetime_str.split(" ")[1][:5]  # HH:MM
    # 标准日盘成线时点
    if time_part in ("10:15", "10:30"):
        return 75  # 09:00 - 10:15
    elif time_part == "11:30":
        return 60  # 10:30 - 11:30
    elif time_part == "14:30":
        return 60  # 13:30 - 14:30
    elif time_part == "15:00":
        return 30  # 14:30 - 15:00
    # 夜盘成线时点
    elif time_part == "22:00":
        return 60  # 21:00 - 22:00
    elif time_part == "23:00":
        return 60  # 22:00 - 23:00
    elif time_part in ("01:00", "00:00"):
        return 60
    elif time_part == "02:30":
        return 30  # 02:00 - 02:30
    return 60  # 默认回退 60 分钟
```
**归一化计算**：
$$\text{norm\_vol} = \frac{\text{volume}}{\text{active\_minutes}} \times 60$$

---

## 4. 核心策略与数学算法规格

### 4.1 日线级客观支撑/阻力区 (S/R Zone) 识别算法
1. **真实波幅计算**：
   $$\text{TR}_t = \max(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|)$$
   $$\text{ATR}_{1D} = \text{SMA}(\text{TR}, 14)$$
2. **动态 ATR-ZigZag 拐点识别**：
   - 设定拐点波幅阈值 $\Delta_{\text{swing}} = 3.0 \times \text{ATR}_{1D}$；
   - 过滤日线微小波动，抽取过去 $N$ 日（默认 120 日）的显著波段高点集 $\{H_k\}$ 与波段低点集 $\{L_k\}$。
3. **邻近极值点聚类与热区生成**：
   - 若两相邻极值点差值 $|P_i - P_j| \le 0.5 \times \text{ATR}_{1D}$，将其聚合为一个水平位 $\bar{P} = \frac{P_i + P_j}{2}$；
   - 每个有效阻力/支撑位向上下扩展形成热区：
     $$\text{Zone} = [\bar{P} - 0.3 \times \text{ATR}_{1D},\ \bar{P} + 0.3 \times \text{ATR}_{1D}]$$
4. **大级别斐波那契空间测算**：
   - 锚定当前这波大趋势起点至当前极值区间；
   - 计算 $38.2\%$ 黄金分割回撤位 $TP_{38.2}$；
   - **硬性空间过滤**：若 $|C_{\text{curr}} - TP_{38.2}| < 2.0 \times \text{ATR}_{1H}$，表明回撤盈利空间受限，日线直接剔除。

### 4.2 小时线级背离与微结构检测算法
#### 4.2.1 波峰波谷定位 (Peak / Trough Detection)
在过去 60 根 1H K 线内：
- 采用 5-bar 局部极值窗口识别最近的两个价格波峰 $P_1$（前高，位于 $t_1$）与 $P_2$（新高，位于 $t_2$）；
- 判定约束：$t_2 - t_1 \ge 5$ 根 Bar，且两峰之间必须存在显著的谷底 $V_{mid}$（其回撤幅度至少 $\ge 1.0 \times \text{ATR}_{1H}$）。

#### 4.2.2 多维背离判定逻辑（以顶背离做空为例，底背离对称）
1. **MACD 双背离（主导必选条件）**：
   - 价格创新高：$\max(H_{P_2}) > \max(H_{P_1})$；
   - 快线背离：$\text{DIF}(P_2) < \text{DIF}(P_1)$；
   - 柱体峰值背离：$\max(\text{MACD\_Hist}_{P_2}) < \max(\text{MACD\_Hist}_{P_1})$；
   - **加分项**：在 $P_1$ 与 $P_2$ 之间，MACD 柱体曾向下穿过零轴。
2. **RSI 背离（加分项，权重 +0.25）**：
   - $\text{RSI}_{14}(P_2) < \text{RSI}_{14}(P_1)$，且 $P_1$ 时 $\text{RSI}_{14}(P_1) \ge 65$。
3. **成交量均量背离（加分项，权重 +0.25）**：
   - 统计 $P_1, P_2$ 各自前后 3 根 K 线的归一化均量 $\overline{\text{norm\_vol}}$：
     $$\overline{\text{norm\_vol}}(P_2) < \overline{\text{norm\_vol}}(P_1) \times 0.85$$
4. **持仓量 (OI) 减仓背离（内盘核心加分项，权重 +0.25）**：
   - 统计 $P_2$ 创新高时段的平均持仓量 $\text{OI}(P_2)$ 与 $P_1$ 时的 $\text{OI}(P_1)$：
     $$\text{OI}(P_2) \le \text{OI}(P_1) \times 0.98$$
   - 物理含义：价格创新高伴随资金减仓，本质为空头踩踏止损推高，无多头增量资金护盘，极易崩塌。

#### 4.2.3 23.6% 斐波那契微结构与止损位
- 锚定区间：极值高点 $H(P_2)$ 到谷底 $L(V_{mid})$；
- **入场触发价 (Entry)**：
  $$\text{Entry} = H(P_2) - 0.236 \times [H(P_2) - L(V_{mid})] - 2 \times \text{tick\_size}$$
- **触发机制**：系统默认以 **1H 收盘价确认突破 Entry** 作为信号触发点，同时输出**盘中触价价位 (Touch Trigger)** 供挂单参考；
- **止损价 (Stop Loss)**：
  $$\text{SL} = H(P_2) + \max(2 \times \text{tick\_size},\ 0.15 \times \text{ATR}_{1H})$$

---

## 5. 风控与交易计划模型规格

### 5.1 以损定仓公式 (Position Sizing)
设定单笔交易账户基准风险金额比例 $R$（默认 $1.0\%$）：
$$\text{Lots} = \left\lfloor \frac{\text{Equity} \times R \times W}{|\text{Entry} - \text{SL}| \times \text{Multiplier}} \right\rfloor$$
其中共振权重系数 $W$ 取值规则：
- 仅 MACD 双背离（1 级信号）：$W = 0.50$；
- MACD + (RSI 或 归一化量能)（2 级信号）：$W = 0.75$；
- MACD + RSI + OI 持仓背离（3 级共振满配）：$W = 1.00$。
- **保护约束**：若计算得出 $\text{Lots} < 1$，直接放弃该笔交易，严禁超额敞口。

### 5.2 分级止盈与追踪执行计划
- **TP1（第一目标位）**：日线大级别波段的 $38.2\%$ 回撤位。抵达后平仓 $60\%$ 仓位，剩余仓位止损移动至保本位（$\text{Entry} \pm 1\ \text{tick}$）；
- **TP2（第二目标位）**：大级别 $66.7\%$ 极限位，同时挂载 1H EMA20 移动均线跟踪止盈（收盘价跌破/升破 EMA20 时市价出场）。

### 5.3 市场环境硬性熔断
1. **强单边加速熔断**：日线 $1\text{D\_ADX} > 35$ 且斜率大于 0 时，全面拦截逆势做空信号；
2. **临近收盘熔断**：信号产生时间距离 15:00 收盘或夜盘最终收盘不足 30 分钟时，标记为收盘风险，不生成新挂单；
3. **涨跌停板熔断**：当前价格距离当日涨跌停板小于 $1.5\%$ 时，禁止开仓。

---

## 6. 输出结构与通知总线规格

### 6.1 结构化 JSON 输出契约 (`reports/YYYYMMDD_HHMM.json`)
```json
{
  "timestamp": "2026-11-21 15:00:00",
  "symbol": "RB0",
  "name": "螺纹钢连续",
  "sector": "黑色系",
  "direction": "SHORT",
  "score": 0.85,
  "resonance_level": 3,
  "divergence_details": {
    "macd_divergence": true,
    "rsi_divergence": true,
    "volume_divergence": true,
    "oi_divergence": true
  },
  "context": {
    "sr_zone": [3480.0, 3510.0],
    "adx_1d": 23.4,
    "sector_sentiment": "中性滞涨"
  },
  "trade_plan": {
    "entry_close_trigger": 3474.0,
    "entry_touch_trigger": 3478.0,
    "stop_loss": 3508.0,
    "risk_per_lot_cny": 340.0,
    "suggested_lots_per_100k": 2,
    "tp1_382": 3415.0,
    "tp2_667": 3350.0,
    "rr_ratio_tp1": 1.88
  }
}
```

### 6.2 CSV 表格归档规范 (方案一：小时线融合日线大级别上下文)
系统每次扫描除输出 JSON 外，自动在 `reports/` 目录导出带有 `utf-8-sig` 编码的 Excel 兼容 CSV 表格，并在 `latest_hourly_signals.csv` 中建立实时快捷入口。

**小时线融合 CSV 字段结构（31 个全维度列）**：
1. **基础信息**：`K线时间`, `合约代码`, `品种名称`, `所属板块`, `交易方向`
2. **日线宏观背景（融合字段）**：`日线优先级`, `日线S/R热区类型`, `日线热区范围`, `距日线热区距离`, `日线ADX趋势值`, `日线至TP1空间`
3. **小时级信号与共振**：`共振星级` (L1/L2/L3), `综合评分`, `现价`
4. **入场与风控点位**：`收盘确认触发价`, `盘中触价触发价`, `止损价`, `止损跳数(ticks)`, `单手风险金额(元)`, `建议手数(10万本金)`, `是否有效下单`
5. **止盈规划与盈亏比**：`第一目标位(TP1_38.2%)`, `TP1平仓比例`, `第二目标位(TP2_66.7%)`, `预期盈亏比(R:R)`
6. **多维背离共振明细**：`MACD双背离`, `MACD跨零轴`, `RSI超买超卖背离`, `成交量均量萎缩背离`, `持仓量(OI)减仓推高背离`
7. **环境风险**：`临近收盘风险`

### 6.3 终端美化看板 (Rich Table)
- 彩色高亮显示：多头信号显示绿色，空头信号显示红色，高等级共振标注 ⭐⭐⭐，增加「日线热区」列。
- 汇总栏展示：扫描品种总数、候选品种数、触发信号数、平均预期盈亏比。

### 6.4 抽象通知通道 (`src/notifier.py`)
- `ConsoleNotifier`：终端美化输出；
- `JsonFileNotifier`：归档到 `reports/`；
- `CsvNotifier`：自动导出最新与历史时间戳 CSV；
- `EmailNotifier` *(已预留)*：支持配置 SMTP 发送 HTML/文本邮件警报。

---

## 7. CLI 命令设计与 Crontab 对接

```bash
# 1. 运行日线级别候选池筛选（每日收盘 15:15 或夜盘前 20:45）
python main.py --daily

# 2. 运行小时线级别信号扫描（每个整点交易结束后触发）
python main.py --hourly

# 3. 扫描指定单个或多个品种
python main.py --hourly --symbols RB0,CU0,IF0

# 4. 查看当前最新筛选看板
python main.py --status
```
