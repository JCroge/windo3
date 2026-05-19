#!/usr/bin/env python3
"""PaperExecutor 单元测试 — 影子账户 / 与实盘并行

验证：
- 开仓/平仓的 equity 计算口径与实盘一致（margin × pnl_pct × lev - fees）
- SL/TP 价格触发自动平仓
- halt 阻止新开仓但保留已有持仓
- 状态持久化（重启后位置和余额恢复）
- 不与实盘 executor 互相影响（消息隔离）
"""

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def setup_temp_paths():
    """为测试隔离持久化路径"""
    tmp = tempfile.mkdtemp(prefix='paper_test_')
    import agents.trading.paper_executor as pe
    pe.PAPER_TRADES_FILE = os.path.join(tmp, 'trades.jsonl')
    pe.PAPER_POSITIONS_FILE = os.path.join(tmp, 'positions.json')
    pe.PAPER_EQUITY_FILE = os.path.join(tmp, 'equity.json')
    return tmp


def make_decision(action, symbol, plan=None, source='judge', confidence=70):
    return {
        'type': 'trade_decision',
        'symbol': symbol,
        'payload': {
            'action': action,
            'symbol': symbol,
            'confidence': confidence,
            'plan': plan,
            'source': source,
            'size_pct': 1.0,
        }
    }


def make_plan(side='long', entry=100.0, sl_dist=0.025, leverage=5, margin=20.0):
    if side == 'long':
        sl = entry * (1 - sl_dist)
        tp = entry * (1 + sl_dist * 2)
    else:
        sl = entry * (1 + sl_dist)
        tp = entry * (1 - sl_dist * 2)
    return {
        'entry_zone': [entry, entry],
        'size_usdt': margin,
        'leverage': leverage,
        'stop_loss': sl,
        'tp_levels': [tp],
        'take_profit': tp,
        'atr_pct': 0.02,
    }


async def _open_helper(pe, symbol, side='long', price=100.0, margin=20.0, leverage=5):
    """直接调用 on_message 模拟消息总线投递（绕过 bus，简化测试）"""
    pe._latest_price[symbol] = price
    plan = make_plan(side=side, entry=price, margin=margin, leverage=leverage)
    action = 'open_long' if side == 'long' else 'open_short'
    await pe.on_message(make_decision(action, symbol, plan=plan))


async def test_open_long_paper():
    """开多：position 写入，equity 扣 entry_fee"""
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()
    eq0 = pe._equity

    await _open_helper(pe, 'BTC-USDT', side='long', price=100, margin=20, leverage=5)

    assert 'BTC-USDT' in pe._positions, "持仓应被记录"
    pos = pe._positions['BTC-USDT']
    assert pos['side'] == 'long'
    assert pos['margin'] == 20
    assert pos['leverage'] == 5
    assert pos['notional'] == 100  # 20 * 5
    # equity 应只扣 entry_fee，不扣 margin（margin 是质押概念）
    assert abs((eq0 - pe._equity) - pos['entry_fee']) < 1e-6, f"equity变动应=entry_fee, got {eq0 - pe._equity} vs {pos['entry_fee']}"
    print(f"  OK open_long: margin={pos['margin']} lev={pos['leverage']} entry_fee={pos['entry_fee']:.4f}")


async def test_open_short_paper():
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()

    await _open_helper(pe, 'ETH-USDT', side='short', price=200, margin=20, leverage=5)
    pos = pe._positions['ETH-USDT']
    assert pos['side'] == 'short'
    assert pos['sl'] > pos['entry_price'], "short 的 SL 必须高于入场"
    assert pos['tp'] < pos['entry_price'], "short 的 TP 必须低于入场"
    print(f"  OK open_short: entry={pos['entry_price']} sl={pos['sl']} tp={pos['tp']}")


async def test_sl_triggers_close():
    """价格跌破 SL → 自动平仓 + PnL 负值"""
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()
    eq0 = pe._equity

    await _open_helper(pe, 'BTC-USDT', side='long', price=100, margin=20, leverage=5)
    pos = pe._positions['BTC-USDT']
    sl = pos['sl']

    # 模拟价格跌破 SL
    await pe.on_message({
        'type': 'price_tick',
        'symbol': 'BTC-USDT',
        'payload': {'symbol': 'BTC-USDT', 'price': sl - 0.01},
    })

    assert 'BTC-USDT' not in pe._positions, "SL 触发后持仓应被清除"
    # PnL 应为负：margin × (-2.5%) × 5 = -2.5 USDT
    expected_gross = 20 * (sl - 100) / 100 * 5  # ≈ -2.5
    assert pe._equity < eq0, "SL 触发后 equity 必须减少"
    print(f"  OK sl_triggers_close: 预期gross_pnl≈{expected_gross:.4f}, equity变动={pe._equity - eq0:.4f}")


async def test_tp_triggers_close():
    """价格上破 TP → 自动平仓 + PnL 正值"""
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()
    eq0 = pe._equity

    await _open_helper(pe, 'BTC-USDT', side='long', price=100, margin=20, leverage=5)
    pos = pe._positions['BTC-USDT']
    tp = pos['tp']

    await pe.on_message({
        'type': 'price_tick',
        'symbol': 'BTC-USDT',
        'payload': {'symbol': 'BTC-USDT', 'price': tp + 0.01},
    })

    assert 'BTC-USDT' not in pe._positions
    assert pe._equity > eq0, f"TP 触发后 equity 必须增加, eq0={eq0:.4f} eq={pe._equity:.4f}"
    print(f"  OK tp_triggers_close: equity变动={pe._equity - eq0:+.4f}")


async def test_state_persists_across_restart():
    """开仓后重建 PaperExecutor，验证 position 和 equity 恢复"""
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe1 = PaperExecutor({'effective_balance_cap': 1000})
    await pe1.setup()
    await _open_helper(pe1, 'BTC-USDT', side='long', price=100, margin=20, leverage=5)
    eq_after_open = pe1._equity
    pos_after_open = dict(pe1._positions['BTC-USDT'])

    # 重建（模拟重启）
    MessageBus.reset()
    pe2 = PaperExecutor({'effective_balance_cap': 1000})
    await pe2.setup()

    assert 'BTC-USDT' in pe2._positions, "重启后持仓应恢复"
    assert abs(pe2._equity - eq_after_open) < 1e-6, f"重启后 equity 应一致: {pe2._equity} vs {eq_after_open}"
    assert pe2._positions['BTC-USDT']['entry_price'] == pos_after_open['entry_price']
    print(f"  OK persist: equity={pe2._equity:.4f} positions={list(pe2._positions.keys())}")


async def test_halt_blocks_new_opens():
    """halt 拒绝新开仓，已有持仓不受影响"""
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()

    # 先开一个仓
    await _open_helper(pe, 'BTC-USDT', side='long', price=100, margin=20, leverage=5)
    assert 'BTC-USDT' in pe._positions

    # 发 halt
    await pe.on_message({'type': 'system_command', 'payload': {'command': 'halt'}})
    assert pe._halted is True

    # 尝试开第二个仓 → 应被拒
    await _open_helper(pe, 'ETH-USDT', side='long', price=200, margin=20, leverage=5)
    assert 'ETH-USDT' not in pe._positions, "halt 后新开仓应被拒"
    assert 'BTC-USDT' in pe._positions, "halt 不影响已有持仓"

    # resume 后恢复
    await pe.on_message({'type': 'system_command', 'payload': {'command': 'resume'}})
    await _open_helper(pe, 'ETH-USDT', side='long', price=200, margin=20, leverage=5)
    assert 'ETH-USDT' in pe._positions, "resume 后应允许开仓"
    print(f"  OK halt/resume 行为正确")


async def test_pnl_matches_live_formula():
    """PnL 公式：gross = margin × pnl_pct × leverage，扣 exit_fee"""
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()
    eq0 = pe._equity

    # 开多 @ 100，平 @ 110 (+10%)，margin=20, lev=5 → gross = 20 × 0.10 × 5 = 10
    await _open_helper(pe, 'BTC-USDT', side='long', price=100, margin=20, leverage=5)
    entry_fee = pe._positions['BTC-USDT']['entry_fee']
    notional = 100  # 20 * 5

    # 用 close 信号直接平（不触发 SL/TP）
    pe._latest_price['BTC-USDT'] = 110
    await pe.on_message({
        'type': 'trade_decision',
        'symbol': 'BTC-USDT',
        'payload': {'action': 'close', 'symbol': 'BTC-USDT', 'confidence': 70, 'source': 'judge'}
    })

    assert 'BTC-USDT' not in pe._positions
    # 实际净结算 = gross_pnl - exit_fee（entry_fee 在开仓时已扣）
    # equity 变动 = -entry_fee + gross_pnl - exit_fee = gross_pnl - entry_fee - exit_fee
    expected_gross = 20 * 0.10 * 5  # = 10
    expected_exit_fee = pe._fee(notional)
    expected_net = expected_gross - entry_fee - expected_exit_fee
    actual_change = pe._equity - eq0
    assert abs(actual_change - expected_net) < 1e-4, \
        f"PnL 公式不一致: expected={expected_net:.4f} actual={actual_change:.4f}"
    print(f"  OK pnl_formula: gross={expected_gross} fees={entry_fee + expected_exit_fee:.4f} net={expected_net:.4f}")


async def test_partial_reduce():
    """部分平仓 50% → margin 减半 + 兑现 50% PnL"""
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()
    eq0 = pe._equity

    await _open_helper(pe, 'BTC-USDT', side='long', price=100, margin=20, leverage=5)
    pe._latest_price['BTC-USDT'] = 110  # +10%

    # PA 发减仓 50%
    await pe.on_message({
        'type': 'trade_decision',
        'symbol': 'BTC-USDT',
        'payload': {
            'action': 'close',
            'symbol': 'BTC-USDT',
            'confidence': 70,
            'source': 'position_analyst',
            'size_pct': 0.5,
        }
    })

    assert 'BTC-USDT' in pe._positions, "部分平仓后持仓仍在"
    assert pe._positions['BTC-USDT']['margin'] == 10, f"margin应减半=10, got {pe._positions['BTC-USDT']['margin']}"
    # 50% 兑现 PnL = 10 × 0.10 × 5 - exit_fee(50)
    assert pe._equity > eq0, "部分兑现应增加 equity（在 fees 之上）"
    print(f"  OK partial_reduce: 剩余margin={pe._positions['BTC-USDT']['margin']} equity={pe._equity:.4f}")


async def test_judge_plan_take_profit_list():
    """回归测试 P0-1：Judge._build_plan() 输出 take_profit 是 list，无 tp_levels

    旧代码 float(plan.get('take_profit', 0)) 在 list 上会抛 TypeError 导致开仓失败。
    """
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()

    # 完全模仿 judge.py:1003-1011 的真实输出（list 形式，无 tp_levels 字段）
    judge_plan = {
        'entry_zone': [100.0, 100.0],
        'stop_loss': 97.5,
        'take_profit': [102.5, 105.0, 107.5],  # 三档止盈
        'size_usdt': 20.0,
        'leverage': 5,
        'atr_pct': 0.02,
        # 注意：没有 tp_levels 字段
    }
    pe._latest_price['BTC-USDT'] = 100.0
    await pe.on_message({
        'type': 'trade_decision',
        'symbol': 'BTC-USDT',
        'payload': {
            'action': 'open_long',
            'symbol': 'BTC-USDT',
            'confidence': 70,
            'plan': judge_plan,
            'source': 'judge',
            'size_pct': 1.0,
        }
    })

    assert 'BTC-USDT' in pe._positions, "Judge plan (take_profit=list) 应成功开仓"
    pos = pe._positions['BTC-USDT']
    assert pos['tp'] == 102.5, f"应取 list 首档作 TP, got {pos['tp']}"
    assert pos['sl'] == 97.5
    print(f"  OK judge_plan_list: tp={pos['tp']} (取首档) sl={pos['sl']}")


async def test_judge_plan_take_profit_scalar_backcompat():
    """回归测试 P0-1：旧 plan 格式 take_profit 是 scalar 仍兼容"""
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    from agents.trading.paper_executor import PaperExecutor

    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()

    legacy_plan = {
        'entry_zone': [100.0, 100.0],
        'stop_loss': 97.5,
        'take_profit': 105.0,  # 旧格式：scalar
        'size_usdt': 20.0,
        'leverage': 5,
        'atr_pct': 0.02,
    }
    pe._latest_price['BTC-USDT'] = 100.0
    await pe.on_message({
        'type': 'trade_decision',
        'symbol': 'BTC-USDT',
        'payload': {
            'action': 'open_long',
            'symbol': 'BTC-USDT',
            'confidence': 70,
            'plan': legacy_plan,
            'source': 'judge',
        }
    })

    assert 'BTC-USDT' in pe._positions
    assert pe._positions['BTC-USDT']['tp'] == 105.0
    print(f"  OK scalar_backcompat: tp={pe._positions['BTC-USDT']['tp']}")


async def test_independent_from_live():
    """publish 用独立 topic paper_execution_result，不污染 execution_result"""
    setup_temp_paths()
    from agents.message_bus import MessageBus
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('listener', ['execution_result', 'paper_execution_result'])

    from agents.trading.paper_executor import PaperExecutor
    pe = PaperExecutor({'effective_balance_cap': 1000})
    await pe.setup()
    await _open_helper(pe, 'BTC-USDT', side='long', price=100, margin=20, leverage=5)

    # 应收到 paper_execution_result，但不应收到 execution_result
    msgs = []
    while True:
        msg = await bus.receive('listener', timeout=0.1)
        if msg is None:
            break
        msgs.append(msg['type'])

    assert 'paper_execution_result' in msgs, f"应发布 paper_execution_result, got {msgs}"
    assert 'execution_result' not in msgs, f"PaperExecutor 不能污染 execution_result, got {msgs}"
    print(f"  OK 独立 topic: 发布{msgs}")


async def run_all():
    tests = [
        test_open_long_paper,
        test_open_short_paper,
        test_sl_triggers_close,
        test_tp_triggers_close,
        test_state_persists_across_restart,
        test_halt_blocks_new_opens,
        test_pnl_matches_live_formula,
        test_partial_reduce,
        test_judge_plan_take_profit_list,
        test_judge_plan_take_profit_scalar_backcompat,
        test_independent_from_live,
    ]
    print("=== PaperExecutor 测试 ===\n")
    passed = 0
    failed = []
    for t in tests:
        try:
            await t()
            passed += 1
        except Exception as e:
            import traceback
            failed.append((t.__name__, str(e)))
            print(f"  FAIL {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n=== 结果: {passed}/{len(tests)} 通过 ===")
    if failed:
        for name, err in failed:
            print(f"  失败: {name} - {err}")
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(run_all())
