"""
Unit tests for symbols loading and filtering logic.
"""

from src.config import load_config
from src.symbols import (
    load_default_symbol_mapping,
    get_symbol_metadata,
    get_active_symbols,
)


def test_load_default_symbol_mapping():
    mapping = load_default_symbol_mapping()
    assert "SHFE" in mapping
    assert "DCE" in mapping
    assert "CZCE" in mapping
    assert "CFFEX" in mapping
    assert "GFEX" in mapping

    shfe_symbols = [item["symbol"] for item in mapping["SHFE"]]
    assert "RB0" in shfe_symbols
    assert "CU0" in shfe_symbols

    gfex_symbols = [item["symbol"] for item in mapping["GFEX"]]
    assert "LC0" in gfex_symbols
    assert "SI0" in gfex_symbols


def test_get_symbol_metadata():
    meta = get_symbol_metadata()
    assert "RB0" in meta
    assert meta["RB0"]["exchange"] == "SHFE"
    assert "螺纹钢" in meta["RB0"]["name"]


def test_get_active_symbols_override():
    config = load_config()
    active = get_active_symbols(config, symbol_override="rb0, cu0, rb0, if0")
    assert active == ["RB0", "CU0", "IF0"]


def test_get_active_symbols_with_excludes():
    config = load_config()
    config.symbols.use_default_continuous = False
    config.symbols.custom_symbols = ["RB0", "CU0", "IF0"]
    config.symbols.exclude_symbols = ["CU0"]

    active = get_active_symbols(config)
    assert active == ["RB0", "IF0"]
