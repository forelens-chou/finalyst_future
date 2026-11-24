"""
Unit tests for CLI parsing and main command dispatcher.
"""

from unittest.mock import patch
import pytest

from main import parse_arguments


def test_cli_parse_all():
    with patch("sys.argv", ["main.py", "--all", "--days", "180"]):
        args = parse_arguments()
        assert args.all is True
        assert args.days == 180
        assert args.period is None


def test_cli_parse_period_and_symbols():
    with patch("sys.argv", ["main.py", "--period", "60m", "--symbols", "RB0,CU0"]):
        args = parse_arguments()
        assert args.period == "60m"
        assert args.symbols == "RB0,CU0"


def test_cli_parse_check_proxy():
    with patch("sys.argv", ["main.py", "--check-proxy"]):
        args = parse_arguments()
        assert args.check_proxy is True


def test_cli_parse_status():
    with patch("sys.argv", ["main.py", "--status"]):
        args = parse_arguments()
        assert args.status is True


def test_cli_parse_resume_and_incremental():
    with patch("sys.argv", ["main.py", "--resume"]):
        args = parse_arguments()
        assert args.resume is True

    with patch("sys.argv", ["main.py", "-i", "--period", "1d"]):
        args = parse_arguments()
        assert args.incremental is True
        assert args.period == "1d"
