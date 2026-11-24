"""
Configuration loader and models for divergence strategy system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

logger = logging.getLogger("divergence.config")


@dataclass
class InstrumentMeta:
    symbol: str
    name: str = ""
    exchange: str = ""
    multiplier: float = 10.0
    tick_size: float = 1.0
    sector: str = "其他"
    limit_ratio: float = 0.08


@dataclass
class DailyScreenerConfig:
    atr_period: int = 14
    zigzag_atr_multiplier: float = 3.0
    zone_merge_atr_multiplier: float = 0.5
    zone_width_atr_multiplier: float = 0.3
    near_zone_atr_multiplier: float = 1.0
    min_space_to_tp1_atr_multiplier: float = 2.0
    donchian_periods: List[int] = field(default_factory=lambda: [20, 60])


@dataclass
class MACDConfig:
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    require_double_divergence: bool = True
    bonus_cross_zero: bool = True


@dataclass
class RSIConfig:
    enabled: bool = True
    period: int = 14
    overbought: float = 65.0
    oversold: float = 35.0


@dataclass
class VolumeConfig:
    enabled: bool = True
    window_bars: int = 3
    shrink_ratio: float = 0.85


@dataclass
class OpenInterestConfig:
    enabled: bool = True
    max_oi_ratio: float = 0.98


@dataclass
class HourlyScannerConfig:
    lookback_bars: int = 60
    min_peak_distance: int = 5
    min_trough_depth_atr_multiplier: float = 1.0
    macd: MACDConfig = field(default_factory=MACDConfig)
    rsi: RSIConfig = field(default_factory=RSIConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)
    open_interest: OpenInterestConfig = field(default_factory=OpenInterestConfig)


@dataclass
class ADXFilterConfig:
    enabled: bool = True
    period: int = 14
    threshold: float = 35.0


@dataclass
class SectorResonanceConfig:
    enabled: bool = True
    warning_threshold: float = 0.6


@dataclass
class FiltersConfig:
    adx: ADXFilterConfig = field(default_factory=ADXFilterConfig)
    sector_resonance: SectorResonanceConfig = field(default_factory=SectorResonanceConfig)
    market_close_buffer_minutes: int = 30
    limit_price_buffer_percent: float = 1.5


@dataclass
class RiskConfig:
    account_equity: float = 100000.0
    base_risk_ratio: float = 0.01
    resonance_weights: Dict[str, float] = field(default_factory=lambda: {
        "level_1": 0.50,
        "level_2": 0.75,
        "level_3": 1.00,
    })
    entry_mode: str = "close"
    buffer_ticks: int = 2
    stop_loss_atr_multiplier: float = 0.15
    tp1_ratio: float = 0.382
    tp1_close_percent: float = 0.60
    tp2_ratio: float = 0.667
    trailing_stop_ema_period: int = 20


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_server: str = ""
    smtp_port: int = 465
    use_ssl: bool = True
    sender: str = ""
    password: str = ""
    recipients: List[str] = field(default_factory=list)


@dataclass
class OutputConfig:
    report_dir: str = "reports"
    enable_console_rich: bool = True
    enable_json_archive: bool = True
    enable_csv_archive: bool = True
    email: EmailConfig = field(default_factory=EmailConfig)


@dataclass
class StrategyConfig:
    db_path: str = "../fetchfu_db/data/futures_data.db"
    history_days_daily: int = 120
    history_days_hourly: int = 30
    screener: DailyScreenerConfig = field(default_factory=DailyScreenerConfig)
    scanner: HourlyScannerConfig = field(default_factory=HourlyScannerConfig)
    filters: FiltersConfig = field(default_factory=FiltersConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    instruments: Dict[str, InstrumentMeta] = field(default_factory=dict)

    @property
    def resolved_db_path(self) -> Path:
        base = Path(__file__).resolve().parent.parent
        p = Path(self.db_path)
        if not p.is_absolute():
            p = (base / p).resolve()
        return p

    @property
    def resolved_report_dir(self) -> Path:
        base = Path(__file__).resolve().parent.parent
        p = Path(self.output.report_dir)
        if not p.is_absolute():
            p = (base / p).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_instruments(file_path: Optional[Path | str] = None) -> Dict[str, InstrumentMeta]:
    if file_path is None:
        file_path = Path(__file__).resolve().parent.parent / "config" / "instrument_config.json"
    p = Path(file_path)
    if not p.exists():
        logger.warning(f"Instrument config not found at {p}")
        return {}
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)
    instruments = {}
    for sym, data in raw.items():
        instruments[sym.upper().strip()] = InstrumentMeta(
            symbol=sym.upper().strip(),
            name=data.get("name", sym),
            exchange=data.get("exchange", ""),
            multiplier=float(data.get("multiplier", 10.0)),
            tick_size=float(data.get("tick_size", 1.0)),
            sector=data.get("sector", "其他"),
            limit_ratio=float(data.get("limit_ratio", 0.08)),
        )
    return instruments


def load_config(config_path: Optional[Path | str] = None) -> StrategyConfig:
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    p = Path(config_path).resolve()
    if not p.exists():
        logger.warning(f"Config file not found at {p}; using default settings.")
        raw_cfg = {}
    else:
        with open(p, "r", encoding="utf-8") as f:
            raw_cfg = yaml.safe_load(f) or {}

    data_cfg = raw_cfg.get("data", {})
    screener_cfg = raw_cfg.get("screener", {}).get("daily", {})
    scanner_cfg = raw_cfg.get("scanner", {}).get("hourly", {})
    macd_cfg = scanner_cfg.get("macd", {})
    rsi_cfg = scanner_cfg.get("rsi", {})
    vol_cfg = scanner_cfg.get("volume", {})
    oi_cfg = scanner_cfg.get("open_interest", {})
    filter_cfg = raw_cfg.get("filters", {})
    risk_cfg = raw_cfg.get("risk", {})
    out_cfg = raw_cfg.get("output", {})
    email_cfg = out_cfg.get("email", {})

    instruments = load_instruments()

    cfg = StrategyConfig(
        db_path=data_cfg.get("db_path", "../fetchfu_db/data/futures_data.db"),
        history_days_daily=int(data_cfg.get("history_days_daily", 120)),
        history_days_hourly=int(data_cfg.get("history_days_hourly", 30)),
        screener=DailyScreenerConfig(
            atr_period=int(screener_cfg.get("atr_period", 14)),
            zigzag_atr_multiplier=float(screener_cfg.get("zigzag_atr_multiplier", 3.0)),
            zone_merge_atr_multiplier=float(screener_cfg.get("zone_merge_atr_multiplier", 0.5)),
            zone_width_atr_multiplier=float(screener_cfg.get("zone_width_atr_multiplier", 0.3)),
            near_zone_atr_multiplier=float(screener_cfg.get("near_zone_atr_multiplier", 1.0)),
            min_space_to_tp1_atr_multiplier=float(screener_cfg.get("min_space_to_tp1_atr_multiplier", 2.0)),
            donchian_periods=screener_cfg.get("donchian_periods", [20, 60]),
        ),
        scanner=HourlyScannerConfig(
            lookback_bars=int(scanner_cfg.get("lookback_bars", 60)),
            min_peak_distance=int(scanner_cfg.get("min_peak_distance", 5)),
            min_trough_depth_atr_multiplier=float(scanner_cfg.get("min_trough_depth_atr_multiplier", 1.0)),
            macd=MACDConfig(
                fast_period=int(macd_cfg.get("fast_period", 12)),
                slow_period=int(macd_cfg.get("slow_period", 26)),
                signal_period=int(macd_cfg.get("signal_period", 9)),
                require_double_divergence=bool(macd_cfg.get("require_double_divergence", True)),
                bonus_cross_zero=bool(macd_cfg.get("bonus_cross_zero", True)),
            ),
            rsi=RSIConfig(
                enabled=bool(rsi_cfg.get("enabled", True)),
                period=int(rsi_cfg.get("period", 14)),
                overbought=float(rsi_cfg.get("overbought", 65.0)),
                oversold=float(rsi_cfg.get("oversold", 35.0)),
            ),
            volume=VolumeConfig(
                enabled=bool(vol_cfg.get("enabled", True)),
                window_bars=int(vol_cfg.get("window_bars", 3)),
                shrink_ratio=float(vol_cfg.get("shrink_ratio", 0.85)),
            ),
            open_interest=OpenInterestConfig(
                enabled=bool(oi_cfg.get("enabled", True)),
                max_oi_ratio=float(oi_cfg.get("max_oi_ratio", 0.98)),
            ),
        ),
        filters=FiltersConfig(
            adx=ADXFilterConfig(
                enabled=bool(filter_cfg.get("adx", {}).get("enabled", True)),
                period=int(filter_cfg.get("adx", {}).get("period", 14)),
                threshold=float(filter_cfg.get("adx", {}).get("threshold", 35.0)),
            ),
            sector_resonance=SectorResonanceConfig(
                enabled=bool(filter_cfg.get("sector_resonance", {}).get("enabled", True)),
                warning_threshold=float(filter_cfg.get("sector_resonance", {}).get("warning_threshold", 0.6)),
            ),
            market_close_buffer_minutes=int(filter_cfg.get("market_close_buffer_minutes", 30)),
            limit_price_buffer_percent=float(filter_cfg.get("limit_price_buffer_percent", 1.5)),
        ),
        risk=RiskConfig(
            account_equity=float(risk_cfg.get("account_equity", 100000.0)),
            base_risk_ratio=float(risk_cfg.get("base_risk_ratio", 0.01)),
            resonance_weights=risk_cfg.get("resonance_weights", {"level_1": 0.5, "level_2": 0.75, "level_3": 1.0}),
            entry_mode=str(risk_cfg.get("entry_mode", "close")),
            buffer_ticks=int(risk_cfg.get("buffer_ticks", 2)),
            stop_loss_atr_multiplier=float(risk_cfg.get("stop_loss_atr_multiplier", 0.15)),
            tp1_ratio=float(risk_cfg.get("tp1_ratio", 0.382)),
            tp1_close_percent=float(risk_cfg.get("tp1_close_percent", 0.60)),
            tp2_ratio=float(risk_cfg.get("tp2_ratio", 0.667)),
            trailing_stop_ema_period=int(risk_cfg.get("trailing_stop_ema_period", 20)),
        ),
        output=OutputConfig(
            report_dir=str(out_cfg.get("report_dir", "reports")),
            enable_console_rich=bool(out_cfg.get("enable_console_rich", True)),
            enable_json_archive=bool(out_cfg.get("enable_json_archive", True)),
            enable_csv_archive=bool(out_cfg.get("enable_csv_archive", True)),
            email=EmailConfig(
                enabled=bool(email_cfg.get("enabled", False)),
                smtp_server=str(email_cfg.get("smtp_server", "")),
                smtp_port=int(email_cfg.get("smtp_port", 465)),
                use_ssl=bool(email_cfg.get("use_ssl", True)),
                sender=str(email_cfg.get("sender", "")),
                password=str(email_cfg.get("password", "")),
                recipients=email_cfg.get("recipients", []),
            ),
        ),
        instruments=instruments,
    )
    return cfg
