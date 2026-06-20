"""fix-phantom-position-resync: 幽灵持仓补录双确认 + 症状硬化单测。"""
from unittest.mock import MagicMock

from executor import ContractExecutor


def test_config_resync_confirm_ticks_default():
    from utils.config_loader import DEFAULTS
    assert DEFAULTS.get("position_resync_confirm_ticks") == 2
