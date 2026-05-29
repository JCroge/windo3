"""AC-P1-007 ~ AC-P1-010 STATE_NAMESPACE 状态文件命名空间契约测试

覆盖：
- 默认（live）路径完全兼容历史
- USE_TESTNET=true 自动切到 testnet namespace
- STATE_NAMESPACE=paper 显式切到 paper namespace
- STATE_NAMESPACE=live 显式时不被 USE_TESTNET 干扰
- 异常 namespace 值回退 live
- 启动 banner 包含 namespace 与各状态文件路径
- ContractExecutor / RiskManager / PortfolioRiskGuard / LiveLedger / HaltState 默认路径都跟随 namespace
"""
import os
import json
import importlib

import pytest

import utils.state_paths as sp_mod
from utils.state_paths import StatePaths, get_state_paths, reset_state_paths


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_state_paths()
    yield
    reset_state_paths()


# --- AC-P1-010 live 默认兼容 ---

def test_live_default_paths_unchanged(monkeypatch):
    monkeypatch.delenv('STATE_NAMESPACE', raising=False)
    monkeypatch.delenv('USE_TESTNET', raising=False)
    reset_state_paths()
    paths = get_state_paths()
    assert paths.namespace == 'live'
    assert paths.positions == 'data/positions.json'
    assert paths.risk_state == 'data/risk_state.json'
    assert paths.riskguard_state == 'data/riskguard_state.json'
    assert paths.halt_state == 'data/halt_state.json'
    assert paths.live_order_events == 'data/live_order_events.jsonl'
    assert paths.live_position_lifecycle == 'data/live_position_lifecycle.json'


def test_live_default_when_use_testnet_false(monkeypatch):
    monkeypatch.setenv('USE_TESTNET', 'false')
    monkeypatch.delenv('STATE_NAMESPACE', raising=False)
    reset_state_paths()
    assert get_state_paths().namespace == 'live'
    assert get_state_paths().positions == 'data/positions.json'


# --- AC-P1-007 testnet 路径隔离 ---

def test_use_testnet_true_routes_to_testnet(monkeypatch):
    monkeypatch.setenv('USE_TESTNET', 'true')
    monkeypatch.delenv('STATE_NAMESPACE', raising=False)
    reset_state_paths()
    paths = get_state_paths()
    assert paths.namespace == 'testnet'
    assert paths.positions == 'data/testnet_positions.json'
    assert paths.risk_state == 'data/testnet_risk_state.json'
    assert paths.halt_state == 'data/testnet_halt_state.json'
    assert paths.live_order_events == 'data/testnet_live_order_events.jsonl'
    assert paths.live_position_lifecycle == 'data/testnet_live_position_lifecycle.json'


def test_explicit_testnet_namespace_overrides_use_testnet(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'testnet')
    monkeypatch.setenv('USE_TESTNET', 'false')
    reset_state_paths()
    assert get_state_paths().namespace == 'testnet'
    assert get_state_paths().positions == 'data/testnet_positions.json'


# --- AC-P1-008 paper 路径隔离 ---

def test_paper_namespace(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'paper')
    monkeypatch.delenv('USE_TESTNET', raising=False)
    reset_state_paths()
    paths = get_state_paths()
    assert paths.namespace == 'paper'
    assert paths.positions == 'data/paper_positions.json'
    assert paths.risk_state == 'data/paper_risk_state.json'
    assert paths.riskguard_state == 'data/paper_riskguard_state.json'
    # paper 不写 live ledger
    assert 'paper_' in paths.live_order_events
    assert paths.live_order_events != 'data/live_order_events.jsonl'


def test_explicit_live_namespace_when_use_testnet_true(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'live')
    monkeypatch.setenv('USE_TESTNET', 'true')
    reset_state_paths()
    # 显式 live 优先级高于 USE_TESTNET 推断
    assert get_state_paths().namespace == 'live'


def test_invalid_namespace_falls_back_to_live(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'staging')  # 非白名单
    monkeypatch.delenv('USE_TESTNET', raising=False)
    reset_state_paths()
    assert get_state_paths().namespace == 'live'


def test_namespace_is_case_insensitive(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'TESTNET')
    reset_state_paths()
    assert get_state_paths().namespace == 'testnet'


# --- AC-P1-009 banner 打印 namespace 与路径 ---

def test_banner_includes_namespace_and_paths(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'testnet')
    reset_state_paths()
    from utils.config_loader import format_banner
    cfg = {
        'use_testnet': True, 'exchange': 'okx', 'leverage': 3,
        'max_trade_amount': 10.0, 'max_drawdown_pct': 20.0,
        'daily_pnl_hard_stop': -50.0, 'consecutive_loss_limit': 3,
        'research_interval': 14400, 'max_active_symbols': 5,
        'min_confidence': 60, 'min_deferred_signal_score': 45,
        'min_liquidity_score_for_weak_signal': 1,
    }
    banner = format_banner(cfg)
    assert '状态命名空间' in banner
    assert 'TESTNET' in banner
    assert 'data/testnet_positions.json' in banner
    assert 'data/testnet_halt_state.json' in banner
    assert 'data/testnet_live_order_events.jsonl' in banner


def test_banner_live_default(monkeypatch):
    monkeypatch.delenv('STATE_NAMESPACE', raising=False)
    monkeypatch.delenv('USE_TESTNET', raising=False)
    reset_state_paths()
    from utils.config_loader import format_banner
    cfg = {
        'use_testnet': False, 'exchange': 'okx', 'leverage': 3,
        'max_trade_amount': 10.0, 'max_drawdown_pct': 20.0,
        'daily_pnl_hard_stop': -50.0, 'consecutive_loss_limit': 3,
        'research_interval': 14400, 'max_active_symbols': 5,
        'min_confidence': 60, 'min_deferred_signal_score': 45,
        'min_liquidity_score_for_weak_signal': 1,
    }
    banner = format_banner(cfg)
    assert 'LIVE' in banner
    assert 'data/positions.json' in banner
    # live 路径不带 testnet/paper 前缀
    assert 'data/testnet_positions.json' not in banner
    assert 'data/paper_positions.json' not in banner


# --- 集成：消费方读默认路径时跟随 namespace ---

def test_risk_manager_default_state_file_follows_namespace(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'testnet')
    reset_state_paths()
    from risk_manager import RiskManager
    rm = RiskManager()
    assert rm.state_file == 'data/testnet_risk_state.json'


def test_risk_manager_explicit_state_file_wins(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'testnet')
    reset_state_paths()
    from risk_manager import RiskManager
    rm = RiskManager(state_file='/tmp/custom_risk.json')
    assert rm.state_file == '/tmp/custom_risk.json'


def test_halt_state_writes_to_namespaced_path(monkeypatch, tmp_path):
    monkeypatch.setenv('STATE_NAMESPACE', 'paper')
    reset_state_paths()
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'data').mkdir(exist_ok=True)

    # 强制重新 import halt_state 拿干净的单例
    import utils.halt_state as hs_mod
    hs_mod._instance = None
    hs_mod.HALT_STATE_FILE = None  # 不走 monkeypatch override
    state = hs_mod.HaltState()
    state.halt('test_reason', 'test')

    assert (tmp_path / 'data' / 'paper_halt_state.json').exists()
    # live 路径不应被写
    assert not (tmp_path / 'data' / 'halt_state.json').exists()


def test_live_ledger_default_paths_follow_namespace(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'testnet')
    reset_state_paths()

    from utils.live_ledger import LiveLedger

    class _StubExchange:
        pass

    ledger = LiveLedger(_StubExchange(), logger=None)
    assert ledger.events_path == 'data/testnet_live_order_events.jsonl'
    assert ledger.lifecycle_path == 'data/testnet_live_position_lifecycle.json'


def test_live_ledger_explicit_paths_win(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'testnet')
    reset_state_paths()

    from utils.live_ledger import LiveLedger

    class _StubExchange:
        pass

    ledger = LiveLedger(
        _StubExchange(),
        events_path='/tmp/custom_events.jsonl',
        lifecycle_path='/tmp/custom_lifecycle.json',
    )
    assert ledger.events_path == '/tmp/custom_events.jsonl'
    assert ledger.lifecycle_path == '/tmp/custom_lifecycle.json'


# --- AC-P1-008 paper 不写 live ledger ---

def test_paper_namespace_does_not_share_live_ledger_path(monkeypatch):
    monkeypatch.setenv('STATE_NAMESPACE', 'paper')
    reset_state_paths()
    paths = get_state_paths()
    # 关键不变量：paper 的所有路径 basename 都和 live 不同
    live = StatePaths.for_namespace('live')
    for field in ('positions', 'risk_state', 'riskguard_state', 'halt_state',
                  'live_order_events', 'live_position_lifecycle'):
        assert getattr(paths, field) != getattr(live, field), \
            f"paper.{field} 与 live 路径冲突: {getattr(paths, field)}"
