"""
Symbol management and resolution module for domestic Chinese futures.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import AppConfig

logger = logging.getLogger("fetchfu.symbols")


def load_default_symbol_mapping(symbols_file: Optional[Path | str] = None) -> Dict[str, List[Dict[str, str]]]:
    """
    Load the exchange-to-symbols mapping from the default JSON file.
    """
    if symbols_file is None:
        root_dir = Path(__file__).resolve().parent.parent
        target_path = root_dir / "config" / "symbols.default.json"
    else:
        target_path = Path(symbols_file)

    if not target_path.exists():
        logger.warning(f"Default symbols file not found at {target_path}")
        return {}

    with open(target_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def get_symbol_metadata(symbols_file: Optional[Path | str] = None) -> Dict[str, Dict[str, str]]:
    """
    Return a lookup map of symbol -> {"name": str, "exchange": str}.
    """
    mapping = load_default_symbol_mapping(symbols_file)
    meta: Dict[str, Dict[str, str]] = {}
    for exchange, items in mapping.items():
        for item in items:
            sym = item.get("symbol", "").upper().strip()
            if sym:
                meta[sym] = {
                    "name": item.get("name", sym),
                    "exchange": exchange,
                }
    return meta


def get_active_symbols(
    config: AppConfig,
    symbol_override: Optional[List[str] | str] = None,
) -> List[str]:
    """
    Resolve the final active list of futures symbols to be fetched.

    Priority:
    1. If symbol_override is provided, use it (parsed from comma-separated string or list).
    2. Otherwise, start with default continuous symbols if `use_default_continuous` is True.
    3. Append `custom_symbols` from configuration.
    4. Remove any symbols in `exclude_symbols`.
    5. Deduplicate while preserving order.
    """
    if symbol_override is not None:
        if isinstance(symbol_override, str):
            raw_list = [s.strip().upper() for s in symbol_override.split(",") if s.strip()]
        else:
            raw_list = [str(s).strip().upper() for s in symbol_override if str(s).strip()]
        # Remove duplicates while preserving order
        seen = set()
        active = []
        for s in raw_list:
            if s not in seen:
                seen.add(s)
                active.append(s)
        logger.info(f"Using CLI symbol override: {active}")
        return active

    symbols: List[str] = []

    # 1. Load default continuous symbols if enabled
    if config.symbols.use_default_continuous:
        symbols_file = config.root_dir / "config" / "symbols.default.json"
        mapping = load_default_symbol_mapping(symbols_file)
        for exchange, items in mapping.items():
            for item in items:
                sym = item.get("symbol", "").upper().strip()
                if sym and sym not in symbols:
                    symbols.append(sym)

    # 2. Append custom symbols
    for s in config.symbols.custom_symbols:
        sym = s.upper().strip()
        if sym and sym not in symbols:
            symbols.append(sym)

    # 3. Exclude specified symbols
    excludes = {s.upper().strip() for s in config.symbols.exclude_symbols if s.strip()}
    final_symbols = [s for s in symbols if s not in excludes]

    logger.info(f"Resolved {len(final_symbols)} active symbols to fetch.")
    return final_symbols
