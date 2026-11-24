from unittest.mock import patch
import pytest

from main import parse_arguments


def test_cli_parsing():
    with patch("sys.argv", ["main.py", "--daily"]):
        args = parse_arguments()
        assert args.daily is True
        assert args.hourly is False

    with patch("sys.argv", ["main.py", "--hourly", "--symbols", "RB0,CU0"]):
        args = parse_arguments()
        assert args.hourly is True
        assert args.symbols == "RB0,CU0"

    with patch("sys.argv", ["main.py", "--status"]):
        args = parse_arguments()
        assert args.status is True
