#!/usr/bin/env python3
"""测试风控管理器"""

from risk_manager import RiskManager


def test_risk_manager():
    """测试风控管理器"""
    print("=== 风控管理器测试 ===\n")

    # 初始化
    rm = RiskManager(
        max_trade_amount=10.0,
        max_drawdown_pct=20.0,
        max_daily_loss=50.0,
        stop_loss_pct=2.0,
        take_profit_pct=5.0
    )

    print("1. 测试风控检查")
    balance = 1000.0
    can_trade, msg = rm.check_can_trade(balance)
    print(f"   余额: {balance} USDT")
    print(f"   可以交易: {can_trade}")
    print(f"   消息: {msg}\n")

    print("2. 测试仓位计算")
    position_size = rm.calculate_position_size(balance)
    print(f"   计算仓位: {position_size} USDT\n")

    print("3. 测试止损止盈计算")
    entry_price = 50000
    stop_loss_long = rm.calculate_stop_loss(entry_price, 'long')
    take_profit_long = rm.calculate_take_profit(entry_price, 'long')
    print(f"   做多入场价: {entry_price}")
    print(f"   止损价: {stop_loss_long} ({-rm.stop_loss_pct}%)")
    print(f"   止盈价: {take_profit_long} (+{rm.take_profit_pct}%)\n")

    print("4. 测试每日亏损限制")
    rm.record_trade(-30.0)
    print(f"   记录亏损: -30 USDT")
    status = rm.get_status()
    print(f"   今日盈亏: {status['daily_pnl']} USDT")
    print(f"   已用额度: {status['daily_loss_used_pct']:.1f}%\n")

    rm.record_trade(-25.0)
    print(f"   再次记录亏损: -25 USDT")
    can_trade, msg = rm.check_can_trade(balance)
    print(f"   可以交易: {can_trade}")
    print(f"   消息: {msg}\n")

    print("5. 测试回撤限制")
    rm2 = RiskManager()
    rm2.check_can_trade(1000)  # 设置峰值
    can_trade, msg = rm2.check_can_trade(750)  # 回撤25%
    print(f"   峰值余额: 1000 USDT")
    print(f"   当前余额: 750 USDT")
    print(f"   可以交易: {can_trade}")
    print(f"   消息: {msg}\n")

    print("✅ 风控管理器测试完成")


if __name__ == '__main__':
    test_risk_manager()
