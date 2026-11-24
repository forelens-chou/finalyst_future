"""
Configuration parsing and dataclass models for fetchfu_db.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass
class ProxyConfig:
    enabled: bool = False
    http_url: str = ""
    https_url: str = ""


@dataclass
class NetworkConfig:
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    timeout_seconds: float = 15.0
    retry_count: int = 3
    retry_backoff_factor: float = 1.5


@dataclass
class DatabaseConfig:
    db_path: str = "data/futures_data.db"
    wal_mode: bool = True


@dataclass
class FetchConfig:
    history_days: int = 365
    hourly_period: str = "60"
    pacing_min_seconds: float = 0.6
    pacing_max_seconds: float = 1.5


@dataclass
class SymbolsConfig:
    use_default_continuous: bool = True
    custom_symbols: List[str] = field(default_factory=list)
    exclude_symbols: List[str] = field(default_factory=list)


@dataclass
class LoggingConfig:
    log_level: str = "INFO"
    log_file: str = "logs/fetchfu.log"
    max_bytes: int = 10485760  # 10MB
    backup_count: int = 5


@dataclass
class AppConfig:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    symbols: SymbolsConfig = field(default_factory=SymbolsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    root_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)

    def resolve_path(self, relative_or_absolute: str | Path) -> Path:
        """Resolve a path against root_dir if relative."""
        p = Path(relative_or_absolute)
        if p.is_absolute():
            return p
        return (self.root_dir / p).resolve()

    @property
    def resolved_db_path(self) -> Path:
        return self.resolve_path(self.database.db_path)

    @property
    def resolved_log_file(self) -> Path:
        return self.resolve_path(self.logging.log_file)


def load_config(config_path: Optional[str | Path] = None) -> AppConfig:
    """
    Load configuration from a YAML file.
    If config_path is None, default to '<project_root>/config/config.yaml'.
    """
    root_dir = Path(__file__).resolve().parent.parent

    if config_path is None:
        target_path = root_dir / "config" / "config.yaml"
    else:
        target_path = Path(config_path)
        if not target_path.is_absolute():
            target_path = (root_dir / target_path).resolve()

    raw_cfg: Dict[str, Any] = {}
    if target_path.exists():
        with open(target_path, "r", encoding="utf-8") as f:
            raw_cfg = yaml.safe_load(f) or {}

    net_raw = raw_cfg.get("network", {})
    proxy_raw = net_raw.get("proxy", {})
    proxy_cfg = ProxyConfig(
        enabled=bool(proxy_raw.get("enabled", False)),
        http_url=str(proxy_raw.get("http_url", "") or ""),
        https_url=str(proxy_raw.get("https_url", "") or ""),
    )
    network_cfg = NetworkConfig(
        proxy=proxy_cfg,
        timeout_seconds=float(net_raw.get("timeout_seconds", 15.0)),
        retry_count=int(net_raw.get("retry_count", 3)),
        retry_backoff_factor=float(net_raw.get("retry_backoff_factor", 1.5)),
    )

    db_raw = raw_cfg.get("database", {})
    db_cfg = DatabaseConfig(
        db_path=str(db_raw.get("db_path", "data/futures_data.db")),
        wal_mode=bool(db_raw.get("wal_mode", True)),
    )

    fetch_raw = raw_cfg.get("fetch", {})
    fetch_cfg = FetchConfig(
        history_days=int(fetch_raw.get("history_days", 365)),
        hourly_period=str(fetch_raw.get("hourly_period", "60")),
        pacing_min_seconds=float(fetch_raw.get("pacing_min_seconds", 0.6)),
        pacing_max_seconds=float(fetch_raw.get("pacing_max_seconds", 1.5)),
    )

    sym_raw = raw_cfg.get("symbols", {})
    sym_cfg = SymbolsConfig(
        use_default_continuous=bool(sym_raw.get("use_default_continuous", True)),
        custom_symbols=list(sym_raw.get("custom_symbols", []) or []),
        exclude_symbols=list(sym_raw.get("exclude_symbols", []) or []),
    )

    log_raw = raw_cfg.get("logging", {})
    log_cfg = LoggingConfig(
        log_level=str(log_raw.get("log_level", "INFO")),
        log_file=str(log_raw.get("log_file", "logs/fetchfu.log")),
        max_bytes=int(log_raw.get("max_bytes", 10485760)),
        backup_count=int(log_raw.get("backup_count", 5)),
    )

    return AppConfig(
        network=network_cfg,
        database=db_cfg,
        fetch=fetch_cfg,
        symbols=sym_cfg,
        logging=log_cfg,
        root_dir=root_dir,
    )


def setup_logger(config: AppConfig, name: str = "fetchfu") -> logging.Logger:
    """
    Configure dual-channel logging: console (stream) and rotating file handler.
    """
    logger = logging.getLogger(name)
    level_name = config.logging.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    # Avoid duplicate handlers if setup_logger called multiple times
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    log_path = config.resolved_log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
