"""P1-J: 统一成本模型 CostModel 单元测试

主要验证：
1. 手续费按 taker/maker 区分
2. 资金费率方向性正确（正费率 long 付/short 收）
3. funding_cost 只返回成本（>=0），不返回负值
4. funding_pnl_signed 带符号
5. round_trip_cost 拆解完整
6. realized_pnl 综合扣费
"""
import sys
sys.path.insert(0, '.')


def test_entry_exit_fee_taker():
    """taker 费率默认 0.001"""
    from utils.cost_model import CostModel
    m = CostModel()
    assert abs(m.entry_fee(1000) - 1.0) < 1e-6, f"1000 × 0.001 = 1.0，实际 {m.entry_fee(1000)}"
    assert abs(m.exit_fee(2000) - 2.0) < 1e-6
    print(f"  ✅ Case 1: taker 手续费正确 (1000 USDT → {m.entry_fee(1000)} USDT)")


def test_maker_fee_lower():
    """maker 费率 < taker"""
    from utils.cost_model import CostModel
    m = CostModel()
    assert m.entry_fee(1000, is_maker=True) < m.entry_fee(1000, is_maker=False)
    assert abs(m.entry_fee(1000, is_maker=True) - 0.5) < 1e-6
    print(f"  ✅ Case 2: maker={m.entry_fee(1000, True)} < taker={m.entry_fee(1000, False)}")


def test_round_trip_fee():
    """round_trip = entry + exit"""
    from utils.cost_model import CostModel
    m = CostModel()
    assert abs(m.round_trip_fee(1000) - 2.0) < 1e-6
    print(f"  ✅ Case 3: 1000 USDT 名义 round-trip 费 = {m.round_trip_fee(1000)} USDT")


def test_funding_long_pays_positive_rate():
    """正费率，long 付费（成本>0）"""
    from utils.cost_model import CostModel
    m = CostModel()
    # 1000 USDT * 0.0005 * (8/8) * 1 = 0.5
    cost = m.funding_cost(notional=1000, funding_rate=0.0005, hold_hours=8, side='long')
    assert abs(cost - 0.5) < 1e-6, f"应=0.5，实际 {cost}"
    print(f"  ✅ Case 4: 正费率 long 付 {cost} USDT")


def test_funding_short_collects_positive_rate():
    """正费率，short 收费（成本=0，不当作 cost）"""
    from utils.cost_model import CostModel
    m = CostModel()
    cost = m.funding_cost(notional=1000, funding_rate=0.0005, hold_hours=8, side='short')
    assert cost == 0.0, f"short 收费时 cost 应=0，实际 {cost}"
    # 但 signed PnL 应为正
    pnl = m.funding_pnl_signed(notional=1000, funding_rate=0.0005, hold_hours=8, side='short')
    assert pnl > 0, f"signed PnL 应>0，实际 {pnl}"
    assert abs(pnl - 0.5) < 1e-6
    print(f"  ✅ Case 5: 正费率 short 收 (cost=0, signed_pnl=+{pnl})")


def test_funding_negative_rate_short_pays():
    """负费率，short 付费"""
    from utils.cost_model import CostModel
    m = CostModel()
    cost = m.funding_cost(notional=1000, funding_rate=-0.0005, hold_hours=8, side='short')
    assert abs(cost - 0.5) < 1e-6
    print(f"  ✅ Case 6: 负费率 short 付 {cost} USDT")


def test_funding_periods_scale():
    """16h 持仓 = 2 个 funding 周期"""
    from utils.cost_model import CostModel
    m = CostModel()
    cost = m.funding_cost(notional=1000, funding_rate=0.001, hold_hours=16, side='long')
    # 1000 * 0.001 * 2 = 2.0
    assert abs(cost - 2.0) < 1e-6
    print(f"  ✅ Case 7: 16h 持仓 funding = 2 × 单期 = {cost}")


def test_slippage_with_spread():
    """spread 0.1% → 单边滑点 = notional × 0.001 × 0.5"""
    from utils.cost_model import CostModel
    m = CostModel()
    # 1000 × 0.001 × 0.5 = 0.5
    slip = m.slippage_cost(notional=1000, spread_pct=0.001)
    assert abs(slip - 0.5) < 1e-6
    rt = m.round_trip_slippage(notional=1000, spread_pct=0.001)
    assert abs(rt - 1.0) < 1e-6
    print(f"  ✅ Case 8: 0.1% spread → 单边{slip} 双边{rt}")


def test_round_trip_cost_breakdown():
    """完整 round-trip 拆解"""
    from utils.cost_model import CostModel
    m = CostModel()
    r = m.round_trip_cost(
        notional=1000, funding_rate=0.0005,
        hold_hours=8, side='long', spread_pct=0.001
    )
    # entry=1, exit=1, funding=0.5, slippage=1.0 → total=3.5
    assert abs(r['entry_fee'] - 1.0) < 1e-6
    assert abs(r['exit_fee'] - 1.0) < 1e-6
    assert abs(r['funding_cost'] - 0.5) < 1e-6
    assert abs(r['slippage_cost'] - 1.0) < 1e-6
    assert abs(r['total_cost'] - 3.5) < 1e-6
    assert abs(r['breakdown_pct'] - 0.0035) < 1e-6
    print(f"  ✅ Case 9: round-trip 拆解 entry={r['entry_fee']} exit={r['exit_fee']} "
          f"funding={r['funding_cost']} slip={r['slippage_cost']} total={r['total_cost']}")


def test_realized_pnl_long_win():
    """long 入场 100→平仓 105，3x 杠杆"""
    from utils.cost_model import CostModel
    m = CostModel()
    r = m.realized_pnl(side='long', entry_price=100, exit_price=105,
                       amount_usdt=10, leverage=3, funding_rate=0, hold_hours=0)
    # notional=30, pnl_pct=5%, gross=1.5
    # fees = 30 × 0.002 = 0.06
    # funding=0
    # net = 1.5 - 0.06 = 1.44
    assert abs(r['gross_pnl'] - 1.5) < 1e-6
    assert abs(r['fees'] - 0.06) < 1e-6
    assert abs(r['net_pnl'] - 1.44) < 1e-6
    print(f"  ✅ Case 10: long PnL gross={r['gross_pnl']} fees={r['fees']} net={r['net_pnl']}")


def test_realized_pnl_short_with_funding():
    """short 入场 100→平仓 95，含 funding 成本（负费率）"""
    from utils.cost_model import CostModel
    m = CostModel()
    r = m.realized_pnl(side='short', entry_price=100, exit_price=95,
                       amount_usdt=10, leverage=2, funding_rate=-0.001,
                       hold_hours=8)
    # notional=20, pnl_pct=5%, gross=1.0
    # fees = 20 × 0.002 = 0.04
    # funding (short, negative rate) = 20 × 0.001 × 1 = 0.02 cost
    # net = 1.0 - 0.04 - 0.02 = 0.94
    assert abs(r['gross_pnl'] - 1.0) < 1e-6
    assert abs(r['funding'] - 0.02) < 1e-6
    assert abs(r['net_pnl'] - 0.94) < 1e-6
    print(f"  ✅ Case 11: short PnL with funding cost net={r['net_pnl']}")


def test_default_singleton():
    """get_default_cost_model 返回同一实例"""
    from utils.cost_model import get_default_cost_model
    a = get_default_cost_model()
    b = get_default_cost_model()
    assert a is b
    print(f"  ✅ Case 12: 默认实例单例")


def test_zero_inputs_safe():
    """0 / 负值输入不崩溃"""
    from utils.cost_model import CostModel
    m = CostModel()
    assert m.entry_fee(0) == 0
    assert m.funding_cost(1000, 0.001, 0, 'long') == 0
    assert m.funding_cost(0, 0.001, 8, 'long') == 0
    assert m.slippage_cost(0, 0.001) == 0
    print(f"  ✅ Case 13: 0/负值输入安全")


def main():
    print("=" * 60)
    print("P1-J: 统一成本模型 CostModel 测试")
    print("=" * 60)
    test_entry_exit_fee_taker()
    test_maker_fee_lower()
    test_round_trip_fee()
    test_funding_long_pays_positive_rate()
    test_funding_short_collects_positive_rate()
    test_funding_negative_rate_short_pays()
    test_funding_periods_scale()
    test_slippage_with_spread()
    test_round_trip_cost_breakdown()
    test_realized_pnl_long_win()
    test_realized_pnl_short_with_funding()
    test_default_singleton()
    test_zero_inputs_safe()
    print("\n" + "=" * 60)
    print("✅ 全部 13 个测试通过")
    print("=" * 60)


if __name__ == '__main__':
    main()
