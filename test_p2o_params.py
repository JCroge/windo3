"""P2-O: 参数稳健调整 测试

主要验证：
1. 默认参数已调稳（fallback win_rate, EV 阈值, 并发上限）
2. _open_positions 在 execution_result 事件下正确增减
3. _make_decision 入口的并发上限检查正确拦截
"""
import sys
import time
import asyncio
sys.path.insert(0, '.')


def _new_judge():
    from agents.trading.judge import MultiJudge
    j = MultiJudge(config={'exchange': 'okx', 'max_trade_amount': 10})
    j._available_balance = 100.0
    return j


def test_default_params_are_conservative():
    """默认参数应该是 P2-O 调稳后的值"""
    j = _new_judge()
    assert j._fallback_win_rate == 0.52, f"fallback 应=0.52，实际 {j._fallback_win_rate}"
    assert j._ev_min_threshold == 0.05, f"EV 阈值应=0.05，实际 {j._ev_min_threshold}"
    assert j._max_concurrent_positions == 3, f"并发上限应=3，实际 {j._max_concurrent_positions}"
    print("  ✅ Case 1: 默认参数已调稳 (fallback=0.52, EV阈值=0.05, 并发=3)")


def test_open_positions_tracked_on_executed():
    """收到 executed/open_long → 加入 _open_positions"""
    j = _new_judge()
    asyncio.get_event_loop().run_until_complete(j.on_message({
        'type': 'execution_result',
        'symbol': 'BTC-USDT',
        'payload': {
            'status': 'executed',
            'action': 'open_long',
            'symbol': 'BTC-USDT',
        }
    }))
    assert 'BTC-USDT' in j._open_positions
    print(f"  ✅ Case 2: executed/open_long → _open_positions={j._open_positions}")


def test_open_positions_removed_on_close():
    """收到 closed_externally / force_closed → 从 _open_positions 移除"""
    j = _new_judge()
    j._open_positions.add('ETH-USDT')

    asyncio.get_event_loop().run_until_complete(j.on_message({
        'type': 'execution_result',
        'symbol': 'ETH-USDT',
        'payload': {
            'status': 'closed_externally',
            'action': 'close',
            'symbol': 'ETH-USDT',
            'direction': 'long',
        }
    }))
    assert 'ETH-USDT' not in j._open_positions
    print("  ✅ Case 3: closed_externally → 从 _open_positions 移除")


def test_symbol_format_unified_on_close():
    """closed_externally 携带 ETH-USDT-SWAP → 也能正确移除 ETH-USDT"""
    j = _new_judge()
    j._open_positions.add('ETH-USDT')

    asyncio.get_event_loop().run_until_complete(j.on_message({
        'type': 'execution_result',
        'symbol': 'ETH-USDT-SWAP',
        'payload': {
            'status': 'closed_externally',
            'action': 'close',
            'direction': 'long',
        }
    }))
    assert 'ETH-USDT' not in j._open_positions, \
        f"to_internal 应剥离 -SWAP，实际仍有 {j._open_positions}"
    print("  ✅ Case 4: SWAP 后缀正确剥离")


def test_concurrent_limit_blocks_new_open():
    """已开仓 3 个 + 新 symbol → _make_decision 入口直接 hold"""
    import logging
    logging.disable(logging.CRITICAL)  # 静默 LLM 等噪声

    j = _new_judge()
    j._open_positions = {'BTC-USDT', 'ETH-USDT', 'SOL-USDT'}

    published = []
    async def mock_publish(*args, **kwargs):
        published.append((args, kwargs))
    j.publish = mock_publish

    # mock _update_balance 避免真实 HTTP 调用
    async def mock_balance():
        j._available_balance = 100.0
    j._update_balance = mock_balance

    tech = {
        'data_quality': {'degraded': False, 'dimensions_ok': 9, 'dimensions_total': 9},
        'indicators': {'price': 1000.0},
        'momentum': {'rsi': 50, 'atr_pct': 0.02, 'volume_ratio': 1.0},
        'trend': {'direction': 'bullish', 'strength': 70, 'higher_tf_bias': 'bullish'},
        'levels': {'support': [990], 'resistance': [1010]},
        'money_flow': {'funding_rate': 0.0001},
        'microstructure': {},
        'rule_signal': {'entry_long': True},
    }
    asyncio.get_event_loop().run_until_complete(j._make_decision('DOGE-USDT', tech))

    assert len(published) == 1
    args, kwargs = published[0]
    decision = args[1]
    assert decision['action'] == 'hold', f"应被并发上限拦截 hold，实际 {decision['action']}"
    assert 'concurrent_limit_reached' in decision.get('risk_warnings', [])
    print(f"  ✅ Case 5: 已 3 仓 + 新 symbol DOGE → hold (reason={decision['reasoning']})")

    logging.disable(logging.NOTSET)


def test_existing_symbol_not_blocked_by_limit():
    """已开仓的 symbol 再来 tick → 不被并发限制拦截（让原有逻辑处理）"""
    import logging
    logging.disable(logging.CRITICAL)

    j = _new_judge()
    j._open_positions = {'BTC-USDT', 'ETH-USDT', 'SOL-USDT'}

    published = []
    async def mock_publish(*args, **kwargs):
        published.append((args, kwargs))
    j.publish = mock_publish

    async def mock_balance():
        j._available_balance = 100.0
    j._update_balance = mock_balance

    # mock LLM 避免真实调用
    async def mock_llm(*args, **kwargs):
        return {'action': 'hold', 'confidence': 50, 'reasoning': 'mock', 'key_factors': [], 'risk_warnings': []}
    j._ask_llm = mock_llm

    tech = {
        'data_quality': {'degraded': False, 'dimensions_ok': 9, 'dimensions_total': 9},
        'indicators': {'price': 1000.0},
        'momentum': {'rsi': 50, 'atr_pct': 0.02, 'volume_ratio': 1.0},
        'trend': {'direction': 'bullish', 'strength': 70, 'higher_tf_bias': 'bullish'},
        'levels': {'support': [990], 'resistance': [1010]},
        'money_flow': {'funding_rate': 0.0001},
        'microstructure': {},
        'rule_signal': {},
    }
    # BTC-USDT 已在 _open_positions —— 应该绕过并发门
    asyncio.get_event_loop().run_until_complete(j._make_decision('BTC-USDT', tech))

    assert len(published) == 1
    args, kwargs = published[0]
    decision = args[1]
    # 即使被其他逻辑拦截也行，但 reason 不应该是 concurrent_limit
    assert 'concurrent_limit_reached' not in decision.get('risk_warnings', []), \
        "已开仓 symbol 不应被并发限制拦截"
    print(f"  ✅ Case 6: 已开仓 BTC-USDT 再来 tick → 不被并发拦截 (reason={decision.get('reasoning','')[:40]})")

    logging.disable(logging.NOTSET)


def main():
    print("=" * 60)
    print("P2-O: 参数稳健调整 测试")
    print("=" * 60)
    test_default_params_are_conservative()
    test_open_positions_tracked_on_executed()
    test_open_positions_removed_on_close()
    test_symbol_format_unified_on_close()
    test_concurrent_limit_blocks_new_open()
    test_existing_symbol_not_blocked_by_limit()
    print("\n" + "=" * 60)
    print("✅ 全部 6 个测试通过")
    print("=" * 60)


if __name__ == '__main__':
    main()
