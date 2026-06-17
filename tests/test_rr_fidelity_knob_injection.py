import os, sys
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.decision_replay import _install_config_flags
from agents.trading.judge import MultiJudge


def _bare_judge():
    # 镜像真实回放调用点 (decision_replay.py:97-100): __new__ + logger 后再装 flag。
    j = MultiJudge.__new__(MultiJudge)
    j.logger = mock.MagicMock()
    return j


def test_install_config_flags_sets_new_knobs():
    j = _bare_judge()
    _install_config_flags(j, {"path_evidence_aligned_enabled": True, "ladder_rr_enabled": True})
    assert j._path_evidence_aligned_enabled is True
    assert j._ladder_rr_enabled is True


def test_install_config_flags_default_lever1_off_lever2_on():
    # trend-entry-levers-default-on: 空 config 兜底须与 judge.__init__ 一致——
    # lever1(path_evidence) 默认关、lever2(ladder) 默认开。
    j = _bare_judge()
    _install_config_flags(j, {})
    assert j._path_evidence_aligned_enabled is False
    assert j._ladder_rr_enabled is True
