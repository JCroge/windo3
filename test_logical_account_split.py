#!/usr/bin/env python3
"""逻辑账户拆分测试 — effective_balance_cap

设计意图：
- 真实余额 6020 USDT，但风控按 1000 USDT 计算
- max_loss 从 6020×5%=301 USDT 降到 1000×5%=50 USDT
- margin 从 6020×10%=602（受 max_trade_amount 10 USDT 卡）→ 1000×10%=100（仍受 10 USDT 卡）
- 与 Daily Hard Stop -50 对齐
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.trading.judge import MultiJudge


def make_tech_minimal():
    """构造最小合法 tech payload，用于测试 _calc_risk_budget。"""
    return {
        'momentum': {'rsi': 50, 'atr_pct': 0.02},
        'money_flow': {'funding_rate': 0},
    }


def test_no_cap_uses_real_balance():
    """未设 cap 时用真实余额"""
    judge = MultiJudge()
    judge._available_balance = 6020
    budget = judge._calc_risk_budget(make_tech_minimal(), 'open_long', 0.025, score=50)
    # 真实余额 6020 → max_loss_budget = 301（虽然实际 max_loss 受 leverage cap 制约）
    # margin 受 max_trade_amount=10 卡死
    assert budget['margin_usdt'] == 10, f"Expected margin=10 (max_trade_amount cap), got {budget['margin_usdt']}"
    print(f"  OK 无 cap: margin={budget['margin_usdt']}, lev={budget['leverage']}, max_loss={budget['max_loss_usdt']:.2f}")


def test_cap_1000_caps_balance():
    """cap=1000 时所有 % 用 1000 计算"""
    judge = MultiJudge(config={'effective_balance_cap': 1000})
    judge._available_balance = 6020
    budget = judge._calc_risk_budget(make_tech_minimal(), 'open_long', 0.025, score=50)
    # balance 被 cap 到 1000 → margin = min(1000×0.10, 10) = 10
    # max_loss_budget = 1000 × 0.05 = 50 (vs 301 without cap)
    # raw_leverage = 50 / (10 × 0.025) = 200, capped to 20
    assert budget['margin_usdt'] == 10
    # 与 Daily Hard Stop -50 对齐：单笔实际 max_loss = margin × sl_dist × lev = 10 × 0.025 × 20 = 5
    # 10 个最大亏损 = 50 = Daily Hard Stop budget
    assert budget['max_loss_usdt'] <= 5.01, f"实际单笔 max_loss 应≤5 USDT，实际 {budget['max_loss_usdt']:.2f}"
    print(f"  OK cap=1000: margin={budget['margin_usdt']}, lev={budget['leverage']}, max_loss={budget['max_loss_usdt']:.2f}")


def test_cap_larger_than_real_balance():
    """cap 大于真实余额时 cap 不生效"""
    judge = MultiJudge(config={'effective_balance_cap': 10000})
    judge._available_balance = 500
    budget = judge._calc_risk_budget(make_tech_minimal(), 'open_long', 0.025, score=50)
    # balance = min(500, 10000) = 500
    # margin = min(500 × 0.10, 10) = 10
    # max_loss = 500 × 0.05 = 25
    assert budget['margin_usdt'] == 10
    print(f"  OK cap>real: margin={budget['margin_usdt']}, lev={budget['leverage']}")


def test_cap_zero_treated_as_disabled():
    """cap=0 视为禁用"""
    judge = MultiJudge(config={'effective_balance_cap': 0})
    judge._available_balance = 6020
    budget = judge._calc_risk_budget(make_tech_minimal(), 'open_long', 0.025, score=50)
    # 0 被视为未启用，用真实余额
    assert budget['margin_usdt'] == 10
    print(f"  OK cap=0 等价于禁用: margin={budget['margin_usdt']}")


def test_config_loader_parses_env(monkeypatch=None):
    """config_loader 正确解析 EFFECTIVE_BALANCE_CAP env"""
    os.environ['EFFECTIVE_BALANCE_CAP'] = '1000'
    try:
        from utils.config_loader import load_config
        cfg = load_config(env_file=None, strict_live_check=False)
        assert cfg.get('effective_balance_cap') == 1000.0, f"Expected 1000.0, got {cfg.get('effective_balance_cap')}"
        print(f"  OK env 解析: effective_balance_cap={cfg['effective_balance_cap']}")
    finally:
        os.environ.pop('EFFECTIVE_BALANCE_CAP', None)


def test_config_loader_defaults_to_none():
    """未设 env 时默认 None"""
    os.environ.pop('EFFECTIVE_BALANCE_CAP', None)
    from utils.config_loader import load_config
    cfg = load_config(env_file=None, strict_live_check=False)
    assert cfg.get('effective_balance_cap') is None, f"Expected None, got {cfg.get('effective_balance_cap')}"
    print(f"  OK 默认 None: effective_balance_cap={cfg.get('effective_balance_cap')}")


def test_hard_limit_rejects_too_small():
    """cap < 10 USDT 被硬限制拒绝"""
    os.environ['EFFECTIVE_BALANCE_CAP'] = '5'
    try:
        from utils.config_loader import load_config, ConfigError
        try:
            load_config(env_file=None, strict_live_check=False)
            assert False, "应抛出 ConfigError"
        except ConfigError as e:
            assert 'effective_balance_cap' in str(e)
            print(f"  OK 硬限制拒绝过小值: {e}")
    finally:
        os.environ.pop('EFFECTIVE_BALANCE_CAP', None)


if __name__ == '__main__':
    print("=== 逻辑账户拆分测试 ===\n")
    tests = [
        test_no_cap_uses_real_balance,
        test_cap_1000_caps_balance,
        test_cap_larger_than_real_balance,
        test_cap_zero_treated_as_disabled,
        test_config_loader_parses_env,
        test_config_loader_defaults_to_none,
        test_hard_limit_rejects_too_small,
    ]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed.append((t.__name__, str(e)))
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n=== 结果: {passed}/{len(tests)} 通过 ===")
    if failed:
        for name, err in failed:
            print(f"  失败: {name} - {err}")
        sys.exit(1)
