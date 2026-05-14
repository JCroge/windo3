"""统一风险预算框架验证测试"""
import sys
sys.path.insert(0, '.')


def test_calc_risk_budget():
    """直接测试 _calc_risk_budget 函数逻辑"""
    from agents.trading.judge import MultiJudge

    judge = MultiJudge(config={'exchange': 'okx', 'max_trade_amount': 10})
    judge._available_balance = 105.0  # 模拟余额
    # margin = min(105*0.10, 10) = 10U

    # 场景1: BTC (低ATR → 高杠杆)
    tech_btc = {
        'money_flow': {'funding_rate': 0.0001},  # 正常费率
        'momentum': {'atr_pct': 0.012},
    }
    sl_dist_btc = 0.023  # 2.3% SL
    budget = judge._calc_risk_budget(tech_btc, 'open_long', sl_dist_btc)
    print(f"[BTC long] lev={budget['leverage']}x size(margin)={budget['size_usdt']:.2f} "
          f"notional={budget['notional_usdt']:.2f} max_loss={budget['max_loss_usdt']:.2f} "
          f"funding={budget['funding_cost_usdt']:.3f} est_hours={budget['est_hold_hours']}")
    assert budget['leverage'] == 20, f"BTC应该20x，实际{budget['leverage']}x"
    assert budget['size_usdt'] == 10.0, f"BTC margin应=10U，实际{budget['size_usdt']}"
    assert budget['notional_usdt'] == 200.0, f"BTC notional应=200U，实际{budget['notional_usdt']}"
    assert budget['max_loss_usdt'] < 5.5, f"BTC max_loss应<5.5U，实际{budget['max_loss_usdt']:.2f}"
    print("  ✅ BTC: 20x杠杆，margin=10U，notional=200U，max_loss合理")

    # 场景2: ZEC (高ATR → 低杠杆)
    tech_zec = {
        'money_flow': {'funding_rate': 0.0002},
        'momentum': {'atr_pct': 0.030},
    }
    sl_dist_zec = 0.049  # 4.9% SL
    budget = judge._calc_risk_budget(tech_zec, 'open_short', sl_dist_zec)
    print(f"\n[ZEC short] lev={budget['leverage']}x size(margin)={budget['size_usdt']:.2f} "
          f"notional={budget['notional_usdt']:.2f} max_loss={budget['max_loss_usdt']:.2f} "
          f"funding={budget['funding_cost_usdt']:.3f} est_hours={budget['est_hold_hours']}")
    assert budget['leverage'] == 10, f"ZEC应该10x，实际{budget['leverage']}x"
    assert budget['size_usdt'] == 10.0, f"ZEC margin应=10U，实际{budget['size_usdt']}"
    assert budget['notional_usdt'] == 100.0, f"ZEC notional应=100U，实际{budget['notional_usdt']}"
    assert budget['max_loss_usdt'] < 5.5, f"ZEC max_loss应<5.5U，实际{budget['max_loss_usdt']:.2f}"
    print("  ✅ ZEC: 10x杠杆，margin=10U，notional=100U，max_loss合理")

    # 场景3: 高资金费率 + BTC long → 成本高
    tech_high_funding = {
        'money_flow': {'funding_rate': 0.001},  # 0.1%/8h 极端费率
        'momentum': {'atr_pct': 0.012},
    }
    budget = judge._calc_risk_budget(tech_high_funding, 'open_long', 0.023)
    notional = budget['notional_usdt']
    gross_profit = notional * 0.046  # 假设tp_dist=4.6%
    effective_rr = (gross_profit - budget['total_cost_usdt']) / (budget['max_loss_usdt'] + budget['total_cost_usdt'])
    print(f"\n[BTC long 高费率] lev={budget['leverage']}x notional={notional:.2f} "
          f"funding_cost={budget['funding_cost_usdt']:.3f} fee={budget['fee_cost_usdt']:.3f} "
          f"total_cost={budget['total_cost_usdt']:.3f}")
    print(f"  gross_profit={gross_profit:.2f} effective_rr={effective_rr:.2f}")
    assert effective_rr < 1.5, f"高费率BTC long应被拒绝(rr<1.5)，实际rr={effective_rr:.2f}"
    print("  ✅ 高费率BTC long: effective_rr<1.5，会被R:R门槛拒绝")

    # 场景4: 高资金费率 + BTC short → 做空收费率，成本低
    budget = judge._calc_risk_budget(tech_high_funding, 'open_short', 0.023)
    notional = budget['notional_usdt']
    gross_profit = notional * 0.046
    effective_rr = (gross_profit - budget['total_cost_usdt']) / (budget['max_loss_usdt'] + budget['total_cost_usdt'])
    print(f"\n[BTC short 高费率] funding_cost={budget['funding_cost_usdt']:.3f} "
          f"total_cost={budget['total_cost_usdt']:.3f} effective_rr={effective_rr:.2f}")
    assert effective_rr > 1.5, f"高费率BTC short应通过(rr>1.5)，实际rr={effective_rr:.2f}"
    print("  ✅ 高费率BTC short: 做空收费率，effective_rr>1.5通过")

    # 场景5: 余额不足（余额5U，margin=min(0.5, 10)=0.5U）
    judge._available_balance = 5.0
    budget = judge._calc_risk_budget(tech_btc, 'open_long', 0.023)
    print(f"\n[余额不足] balance=5U lev={budget['leverage']}x size(margin)={budget['size_usdt']:.2f} "
          f"notional={budget['notional_usdt']:.2f}")
    assert budget['size_usdt'] == 0.5, f"margin应=0.5U，实际{budget['size_usdt']}"
    assert budget['notional_usdt'] == 10.0, f"notional应=10U，实际{budget['notional_usdt']}"
    print("  ✅ 余额不足: 保证金自动缩小")

    # 场景6: ETH (中等ATR → 向下圆整)
    judge._available_balance = 105.0
    tech_eth = {
        'money_flow': {'funding_rate': 0.00015},
        'momentum': {'atr_pct': 0.020},
    }
    budget = judge._calc_risk_budget(tech_eth, 'open_long', 0.035)
    print(f"\n[ETH long] lev={budget['leverage']}x size(margin)={budget['size_usdt']:.2f} "
          f"notional={budget['notional_usdt']:.2f} max_loss={budget['max_loss_usdt']:.2f} "
          f"est_hours={budget['est_hold_hours']}")
    # 0.5/0.035=14.3 → int=14 → 向下圆整到10
    assert budget['leverage'] == 10, f"ETH应该10x，实际{budget['leverage']}x"
    assert budget['max_loss_usdt'] <= 5.25 + 0.1, f"ETH max_loss应≤5.25U，实际{budget['max_loss_usdt']:.2f}"
    print("  ✅ ETH: 10x杠杆，向下圆整保证风险不超预算")

    # 场景7: Executor兼容性验证 — size_usdt不超过max_trade_amount
    judge._available_balance = 200.0  # 大余额
    budget = judge._calc_risk_budget(tech_btc, 'open_long', 0.023)
    print(f"\n[大余额兼容] balance=200U margin={budget['size_usdt']:.2f} "
          f"(应≤max_trade_amount=10)")
    assert budget['size_usdt'] <= 10.0, f"margin应≤10U(max_trade_amount)，实际{budget['size_usdt']}"
    print("  ✅ 大余额: margin受max_trade_amount约束，Executor兼容")

    print("\n" + "="*60)
    print("全部7个场景验证通过！")
    print("="*60)


def test_build_plan_integration():
    """测试 _build_plan 集成（需要完整tech数据）"""
    from agents.trading.judge import MultiJudge

    judge = MultiJudge(config={'exchange': 'okx', 'max_trade_amount': 10})
    judge._available_balance = 105.0

    tech = {
        'levels': {'support': [95000], 'resistance': [105000]},
        'risk': {'leverage_risk': 'low', 'volatility_regime': 'normal', 'liquidity_score': 80},
        'microstructure': {'spread_pct': 0.01},
        'momentum': {'atr_pct': 0.012, 'rsi': 55, 'volume_anomaly': False},
        'trend': {'direction': 'bullish', 'strength': 70},
        'money_flow': {'funding_rate': 0.0001},
        'indicators': {'price': 100000},
    }

    plan = judge._build_plan(tech, 'open_long', 100000, 65)
    print(f"\n[集成测试] BTC open_long @ 100000")
    print(f"  leverage={plan['leverage']}x size(margin)={plan['size_usdt']}")
    print(f"  sl={plan['stop_loss']} tp={plan['take_profit']}")
    print(f"  gross_rr={plan['risk_reward_ratio']} effective_rr={plan['effective_risk_reward_ratio']}")
    print(f"  funding_cost={plan['funding_cost']} est_hours={plan['est_hold_hours']}")

    assert 'effective_risk_reward_ratio' in plan, "plan缺少effective_risk_reward_ratio字段"
    assert 'funding_cost' in plan, "plan缺少funding_cost字段"
    assert plan['leverage'] in [1, 2, 3, 5, 10, 20], f"杠杆不在OKX允许值中: {plan['leverage']}"
    assert plan['size_usdt'] <= 10.0, f"size_usdt(margin)应≤10U: {plan['size_usdt']}"
    print("  ✅ _build_plan 集成测试通过")


def test_executor_compatibility():
    """验证plan字段与Executor兼容"""
    from agents.trading.judge import MultiJudge

    judge = MultiJudge(config={'exchange': 'okx', 'max_trade_amount': 10})
    judge._available_balance = 105.0

    tech = {
        'levels': {'support': [95000], 'resistance': [105000]},
        'risk': {'leverage_risk': 'low', 'volatility_regime': 'normal', 'liquidity_score': 80},
        'microstructure': {'spread_pct': 0.01},
        'momentum': {'atr_pct': 0.012, 'rsi': 55, 'volume_anomaly': False},
        'trend': {'direction': 'bullish', 'strength': 70},
        'money_flow': {'funding_rate': 0.0001},
        'indicators': {'price': 100000},
    }

    plan = judge._build_plan(tech, 'open_long', 100000, 65)

    # Executor读取的字段必须存在且类型正确
    assert isinstance(plan['leverage'], int), f"leverage应为int: {type(plan['leverage'])}"
    assert isinstance(plan['size_usdt'], float), f"size_usdt应为float: {type(plan['size_usdt'])}"
    assert isinstance(plan['order_type'], str), f"order_type应为str: {type(plan['order_type'])}"
    assert isinstance(plan['stop_loss'], (int, float)), f"stop_loss应为数值: {type(plan['stop_loss'])}"
    assert isinstance(plan['take_profit'], list), f"take_profit应为list: {type(plan['take_profit'])}"
    assert isinstance(plan['entry_zone'], list), f"entry_zone应为list: {type(plan['entry_zone'])}"

    # Executor中: contract_value = size_usdt * leverage
    # size_usdt是保证金，乘leverage得名义价值
    contract_value = plan['size_usdt'] * plan['leverage']
    print(f"\n[Executor兼容] margin={plan['size_usdt']} × lev={plan['leverage']} = notional={contract_value}")
    assert contract_value <= 210, f"名义价值过大: {contract_value}"
    assert contract_value >= 10, f"名义价值过小: {contract_value}"
    print("  ✅ Executor兼容性验证通过")


if __name__ == '__main__':
    test_calc_risk_budget()
    test_build_plan_integration()
    test_executor_compatibility()
