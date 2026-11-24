"""
Unit tests for configuration loading and validation.
"""

from pathlib import Path
import tempfile
import yaml
import pytest

from src.config import load_config, AppConfig


def test_load_default_config():
    config = load_config()
    assert isinstance(config, AppConfig)
    assert config.database.db_path == "data/futures_data.db"
    assert config.fetch.hourly_period == "60"
    assert config.symbols.use_default_continuous is True
    assert isinstance(config.resolved_db_path, Path)
    assert isinstance(config.resolved_log_file, Path)


def test_custom_config_override():
    custom_content = {
        "network": {
            "proxy": {
                "enabled": True,
                "http_url": "socks5h://127.0.0.1:9999",
                "https_url": "socks5h://127.0.0.1:9999",
            },
            "timeout_seconds": 25.0,
        },
        "database": {
            "db_path": "custom/test.db",
            "wal_mode": False,
        },
        "fetch": {
            "history_days": 180,
            "pacing_min_seconds": 1.0,
            "pacing_max_seconds": 2.0,
        },
        "symbols": {
            "use_default_continuous": False,
            "custom_symbols": ["RB2410", "IF2412"],
            "exclude_symbols": ["IF2412"],
        },
    }

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(custom_content, f)
        temp_path = f.name

    try:
        cfg = load_config(temp_path)
        assert cfg.network.proxy.enabled is True
        assert cfg.network.proxy.http_url == "socks5h://127.0.0.1:9999"
        assert cfg.network.timeout_seconds == 25.0
        assert cfg.database.db_path == "custom/test.db"
        assert cfg.database.wal_mode is False
        assert cfg.fetch.history_days == 180
        assert cfg.fetch.pacing_min_seconds == 1.0
        assert cfg.fetch.pacing_max_seconds == 2.0
        assert cfg.symbols.use_default_continuous is False
        assert cfg.symbols.custom_symbols == ["RB2410", "IF2412"]
        assert cfg.symbols.exclude_symbols == ["IF2412"]
    finally:
        Path(temp_path).unlink(missing_ok=True)
