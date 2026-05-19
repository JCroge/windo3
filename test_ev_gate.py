"""P2-N: 期望值（EV）前置校验门 单元测试

测试要点：
1. _get_p_win 根据历史样本量返回 rolling/fallback
2. _check_expected_value 在各种 EV/score 组合下的判定
3. _build_plan 输出 expected_value 等字段
"""
import sys
sys.path.insert(0, '.')


def _new_judge():
    """构造一个绕过 exchange 初始化的 MultiJudge 实例"""
    from agents.trading.judge import MultiJudge
    j = MultiJudge(config={'exchange': 'okx', 'max_trade_amount': 10})
    j._available_balance = 100.0
    return j


def test_p_win_fallback_when_history_insufficient():
    """历史 < 10 笔 → 用 fallback"""
    j = _new_judge()
    j._recent_win_rate = 0.3  # 即使 rolling 很低
    j._total_completed_trades = 5  # 但样本不足
    p_win, source = j._get_p_win()
    assert source == 'fallback', f"应该 fallback，实际 {source}"
    # P2-O 调整为 0.52
    assert abs(p_win - 0.52) < 1e-6, f"fallback 应=0.52（P2-O 后），实际 {p_win}"
    print("  ✅ Case 1: 历史不足，用 fallback=0.52")


def test_p_win_rolling_when_sufficient():
    """历史 ≥ 10 笔 → 用 rolling"""
    j = _new_judge()
    j._recent_win_rate = 0.7
    j._total_completed_trades = 20
    p_win, source = j._get_p_win()
    assert source == 'rolling', f"应该 rolling，实际 {source}"
    assert abs(p_win - 0.7) < 1e-6, f"应=0.7，实际 {p_win}"
    print("  ✅ Case 2: 历史充足，用 rolling=0.70")


def test_ev_gate_pass_with_positive_ev():
    """正 EV 通过"""
    j = _new_judge()
    j._recent_win_rate = 0.7
    j._total_completed_trades = 20
    plan = {
        'expected_value': 1.30,
        'p_win_used': 0.7,
        'p_win_source': 'rolling',
        'net_profit_usdt': 2.8,
        'net_loss_usdt': 2.2,
    }
    assert j._check_expected_value('BTC-USDT', plan, score=50.0) is True
    print("  ✅ Case 3: EV=+1.30 通过")


def test_ev_gate_block_with_negative_ev():
    """负 EV 拦截（正常 score）"""
    j = _new_judge()
    j._recent_win_rate = 0.5
    j._total_completed_trades = 20
    plan = {
        'expected_value': -0.5,
        'p_win_used': 0.5,
        'p_win_source': 'rolling',
        'net_profit_usdt': 1.0,
        'net_loss_usdt': 2.0,
    }
    assert j._check_expected_value('BTC-USDT', plan, score=40.0) is False
    print("  ✅ Case 4: EV=-0.5 拦截")


def test_ev_gate_block_when_rolling_winrate_collapsed():
    """rolling 胜率 < 0.4 + score<70 → 强拒（即使 EV 仍正）"""
    j = _new_judge()
    j._recent_win_rate = 0.30
    j._total_completed_trades = 30
    # 构造一个 EV 略正但 win_rate 已崩塌的 plan
    plan = {
        'expected_value': 0.05,
        'p_win_used': 0.30,
        'p_win_source': 'rolling',
        'net_profit_usdt': 5.0,
        'net_loss_usdt': 2.0,
    }
    assert j._check_expected_value('BTC-USDT', plan, score=50.0) is False, \
        "胜率<0.4 + score<70 应该强拒"
    print("  ✅ Case 5: rolling=30%/score=50 → 强拒")


def test_ev_gate_pass_when_rolling_low_but_signal_extreme():
    """rolling<0.4 但 score>=70 极强信号 → 强拒规则放行，转入正常 EV 检查"""
    j = _new_judge()
    j._recent_win_rate = 0.35
    j._total_completed_trades = 30
    # EV 正 + score 极强 → 通过
    plan = {
        'expected_value': 0.10,
        'p_win_used': 0.35,
        'p_win_source': 'rolling',
        'net_profit_usdt': 4.0,
        'net_loss_usdt': 2.0,
    }
    assert j._check_expected_value('BTC-USDT', plan, score=75.0) is True, \
        "rolling<0.4 但 score=75 极强信号且 EV>0 应通过"
    print("  ✅ Case 6: rolling=35%/score=75/EV=+0.10 → 通过")


def test_ev_gate_exemption_for_strong_signal_marginal_ev():
    """正常胜率 + EV 微负 (>=-0.3) + score>=60 强信号 → 豁免通过"""
    j = _new_judge()
    j._recent_win_rate = 0.55
    j._total_completed_trades = 20
    plan = {
        'expected_value': -0.10,  # 微负
        'p_win_used': 0.55,
        'p_win_source': 'rolling',
        'net_profit_usdt': 1.5,
        'net_loss_usdt': 1.7,
    }
    assert j._check_expected_value('BTC-USDT', plan, score=65.0) is True, \
        "EV=-0.10 + score=65 应该豁免通过"
    print("  ✅ Case 7: EV=-0.10/score=65 → 强信号豁免通过")


def test_ev_gate_no_exemption_when_ev_too_negative():
    """EV 太负 (<-0.3) 即使强信号也不豁免"""
    j = _new_judge()
    j._recent_win_rate = 0.55
    j._total_completed_trades = 20
    plan = {
        'expected_value': -0.50,  # 严重负
        'p_win_used': 0.55,
        'p_win_source': 'rolling',
        'net_profit_usdt': 1.0,
        'net_loss_usdt': 2.0,
    }
    assert j._check_expected_value('BTC-USDT', plan, score=70.0) is False, \
        "EV=-0.50 即使 score=70 也应该拒"
    print("  ✅ Case 8: EV=-0.50/score=70 → EV 太负，拒")


def test_build_plan_outputs_ev_fields():
    """_build_plan 输出 expected_value/p_win_used 等字段"""
    j = _new_judge()
    j._available_balance = 100.0
    tech = {
        'indicators': {'price': 100.0},
        'levels': {
            'support': [97.0, 95.0, 93.0],
            'resistance': [103.0, 105.0, 107.0],
        },
        'momentum': {'atr_pct': 0.02, 'rsi': 50, 'volume_ratio': 1.0},
        'trend': {'direction': 'bullish', 'strength': 70, 'higher_tf_bias': 'bullish'},
        'money_flow': {'funding_rate': 0.0001},
        'microstructure': {'spread_pct': 0.01},
    }
    plan = j._build_plan(tech, 'open_long', 100.0, 60, score=50.0)

    assert 'expected_value' in plan, "plan 应包含 expected_value"
    assert 'p_win_used' in plan, "plan 应包含 p_win_used"
    assert 'p_win_source' in plan, "plan 应包含 p_win_source"
    assert 'net_profit_usdt' in plan, "plan 应包含 net_profit_usdt"
    assert 'net_loss_usdt' in plan, "plan 应包含 net_loss_usdt"
    # 启动时无 rolling，应该是 fallback
    assert plan['p_win_source'] == 'fallback'
    # P2-O 调整为 0.52
    assert abs(plan['p_win_used'] - 0.52) < 1e-6

    print(f"  ✅ Case 9: plan={{'expected_value': {plan['expected_value']}, "
          f"'p_win_used': {plan['p_win_used']}, 'p_win_source': '{plan['p_win_source']}', "
          f"'net_profit': {plan['net_profit_usdt']}, 'net_loss': {plan['net_loss_usdt']}}}")


def test_strategy_review_message_updates_state():
    """接收 strategy_review 消息应更新 Judge 的 win_rate 缓存"""
    import asyncio
    j = _new_judge()
    msg = {
        'type': 'strategy_review',
        'payload': {
            'recent_metrics': {
                'win_rate': 0.68,
                'profit_factor': 1.85,
                'total_pnl': 5.2,
            },
            'total_trades': 25,
        }
    }
    # 直接调用 on_message（绕过总线）
    asyncio.get_event_loop().run_until_complete(j.on_message(msg))
    assert j._recent_win_rate == 0.68
    assert j._recent_profit_factor == 1.85
    assert j._total_completed_trades == 25
    print(f"  ✅ Case 10: strategy_review 后 win_rate={j._recent_win_rate}, "
          f"pf={j._recent_profit_factor}, n={j._total_completed_trades}")


def main():
    print("=" * 60)
    print("P2-N: EV 期望值前置校验门 测试")
    print("=" * 60)
    test_p_win_fallback_when_history_insufficient()
    test_p_win_rolling_when_sufficient()
    test_ev_gate_pass_with_positive_ev()
    test_ev_gate_block_with_negative_ev()
    test_ev_gate_block_when_rolling_winrate_collapsed()
    test_ev_gate_pass_when_rolling_low_but_signal_extreme()
    test_ev_gate_exemption_for_strong_signal_marginal_ev()
    test_ev_gate_no_exemption_when_ev_too_negative()
    test_build_plan_outputs_ev_fields()
    test_strategy_review_message_updates_state()
    print("\n" + "=" * 60)
    print("✅ 全部 10 个测试通过")
    print("=" * 60)


if __name__ == '__main__':
    main()
