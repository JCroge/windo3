"""P1-K: MessageBus 优先级 + 背压 + 死信单元测试

验证：
1. 优先级排序：CRITICAL > HIGH > NORMAL > LOW（哪怕 LOW 先到）
2. 同优先级 FIFO（用 seq 计数器保证）
3. 背压 SOFT：q >= 500 丢 LOW
4. 背压 HARD：q >= 1000 丢 NORMAL + LOW
5. CRITICAL/HIGH 即便满队列也不丢
6. 死信队列：未知目标 → DLQ
7. get_metrics 字段完整
8. 向后兼容：旧 publish/register/receive API 仍工作
"""
import sys
import asyncio
sys.path.insert(0, '.')


async def test_priority_ordering():
    """CRITICAL 优先于 LOW，哪怕 LOW 先入队。"""
    from agents.message_bus import MessageBus
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('agent_a', ['market_data', 'risk_alert'])

    await bus.publish('src', 'market_data', {'p': 100}, to='agent_a')
    await bus.publish('src', 'market_data', {'p': 101}, to='agent_a')
    await bus.publish('src', 'risk_alert', {'level': 'high'}, to='agent_a')

    m1 = await bus.receive('agent_a', timeout=0.5)
    m2 = await bus.receive('agent_a', timeout=0.5)
    m3 = await bus.receive('agent_a', timeout=0.5)

    assert m1 is not None and m1['type'] == 'risk_alert', f"应先收到 risk_alert，实际 {m1}"
    assert m2['type'] == 'market_data'
    assert m3['type'] == 'market_data'
    print(f"  ✅ Case 1: CRITICAL 优先于 LOW 出队")


async def test_fifo_within_priority():
    """同优先级按 FIFO（seq）出队。"""
    from agents.message_bus import MessageBus
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('agent_b', ['market_data'])

    for i in range(5):
        await bus.publish('src', 'market_data', {'i': i}, to='agent_b')

    received = []
    for _ in range(5):
        m = await bus.receive('agent_b', timeout=0.5)
        received.append(m['payload']['i'])

    assert received == [0, 1, 2, 3, 4], f"同优先级 FIFO，实际 {received}"
    print(f"  ✅ Case 2: 同优先级 FIFO 顺序 {received}")


async def test_backpressure_soft_drops_low():
    """SOFT 限：q>=500 时丢 LOW，但 NORMAL/HIGH 不丢。"""
    from agents.message_bus import MessageBus
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('agent_c', ['market_data', 'tech_analysis', 'trade_decision'])

    # 灌满到 SOFT 边界附近（500 条 LOW）
    for i in range(500):
        await bus.publish('src', 'market_data', {'i': i}, to='agent_c')

    # 再发 LOW 应被丢
    await bus.publish('src', 'market_data', {'i': 9999}, to='agent_c')
    # NORMAL 应该能进
    await bus.publish('src', 'tech_analysis', {'sig': 'ok'}, to='agent_c')
    # HIGH 应该能进
    await bus.publish('src', 'trade_decision', {'action': 'long'}, to='agent_c')

    m = bus.get_metrics()['agent_c']
    assert m['dropped'] >= 1, f"应至少丢 1 条 LOW，dropped={m['dropped']}"
    print(f"  ✅ Case 3: SOFT 背压 dropped={m['dropped']} pending={m['pending']}")


async def test_backpressure_hard_drops_normal():
    """HARD 限：q>=1000 时丢 LOW + NORMAL，但 HIGH 不丢。"""
    from agents.message_bus import MessageBus
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('agent_d', ['tech_analysis', 'trade_decision'])

    # 灌满到 HARD 边界
    for i in range(1000):
        await bus.publish('src', 'tech_analysis', {'i': i}, to='agent_d')

    pending_before = bus.get_metrics()['agent_d']['pending']
    dropped_before = bus.get_metrics()['agent_d']['dropped']

    # NORMAL 该被丢
    await bus.publish('src', 'tech_analysis', {'i': 9999}, to='agent_d')
    # HIGH 仍能进
    await bus.publish('src', 'trade_decision', {'action': 'short'}, to='agent_d')

    m = bus.get_metrics()['agent_d']
    assert m['dropped'] >= dropped_before + 1, f"HARD 背压应丢 NORMAL，前后 dropped: {dropped_before}→{m['dropped']}"
    print(f"  ✅ Case 4: HARD 背压 dropped={m['dropped']} pending={m['pending']}")


async def test_critical_never_dropped():
    """CRITICAL 即便队列满也能进。"""
    from agents.message_bus import MessageBus
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('agent_e', ['tech_analysis', 'risk_alert'])

    # 灌满到 HARD
    for i in range(1000):
        await bus.publish('src', 'tech_analysis', {'i': i}, to='agent_e')

    # CRITICAL 应该能进
    await bus.publish('src', 'risk_alert', {'level': 'critical'}, to='agent_e')

    # 第一个出来的应该是 CRITICAL
    m = await bus.receive('agent_e', timeout=0.5)
    assert m is not None and m['type'] == 'risk_alert', f"CRITICAL 应优先，实际 {m['type'] if m else None}"
    print(f"  ✅ Case 5: CRITICAL 即满队列也不丢，且优先出队")


async def test_dlq_unknown_target():
    """发到未知 agent → DLQ。"""
    from agents.message_bus import MessageBus
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('agent_f', ['market_data'])

    await bus.publish('src', 'market_data', {'p': 100}, to='nonexistent_agent')

    dlq = bus.get_dead_letters(limit=10)
    assert len(dlq) >= 1, f"DLQ 应有 1 条，实际 {len(dlq)}"
    assert dlq[-1]['reason'] == 'unknown_target'
    assert dlq[-1]['to'] == 'nonexistent_agent'
    print(f"  ✅ Case 6: 未知目标 → DLQ，reason={dlq[-1]['reason']}")


async def test_metrics_fields():
    """get_metrics 返回完整字段。"""
    from agents.message_bus import MessageBus
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('agent_g', ['market_data'])

    await bus.publish('src', 'market_data', {'p': 100}, to='agent_g')
    await bus.publish('src', 'market_data', {'p': 101}, to='agent_g')
    await bus.receive('agent_g', timeout=0.5)

    m = bus.get_metrics()
    assert 'agent_g' in m
    g = m['agent_g']
    assert 'dropped' in g and 'dlq' in g
    assert 'total_in' in g and 'total_out' in g and 'pending' in g
    assert g['total_in'] == 2, f"total_in 应=2，实际 {g['total_in']}"
    assert g['total_out'] == 1, f"total_out 应=1，实际 {g['total_out']}"
    assert g['pending'] == 1, f"pending 应=1，实际 {g['pending']}"
    assert '_dlq_size' in m
    print(f"  ✅ Case 7: metrics 字段完整 {g}")


async def test_backward_compat_broadcast():
    """旧 broadcast 行为兼容（无 to 参数）。"""
    from agents.message_bus import MessageBus
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('agent_h1', ['tech_analysis'])
    bus.register('agent_h2', ['tech_analysis'])

    await bus.publish('src', 'tech_analysis', {'sig': 'ok'})

    m1 = await bus.receive('agent_h1', timeout=0.5)
    m2 = await bus.receive('agent_h2', timeout=0.5)
    assert m1 is not None and m1['type'] == 'tech_analysis'
    assert m2 is not None and m2['type'] == 'tech_analysis'
    print(f"  ✅ Case 8: 广播向后兼容（无 to 参数）")


async def test_explicit_priority_override():
    """显式 priority 可覆盖默认推断。"""
    from agents.message_bus import MessageBus, PRIORITY_CRITICAL
    MessageBus.reset()
    bus = MessageBus.get_instance()
    bus.register('agent_i', ['tech_analysis'])

    # tech_analysis 默认 NORMAL，但显式设为 CRITICAL
    await bus.publish('src', 'tech_analysis', {'i': 1}, to='agent_i')  # NORMAL
    await bus.publish('src', 'tech_analysis', {'i': 2}, to='agent_i', priority=PRIORITY_CRITICAL)

    m1 = await bus.receive('agent_i', timeout=0.5)
    m2 = await bus.receive('agent_i', timeout=0.5)
    assert m1['payload']['i'] == 2, f"显式 CRITICAL 应先出，实际 {m1['payload']}"
    assert m2['payload']['i'] == 1
    print(f"  ✅ Case 9: 显式 priority 覆盖默认推断")


async def test_unknown_msg_type_defaults_normal():
    """未在映射表中的 msg_type 默认 NORMAL 优先级。"""
    from agents.message_bus import get_priority, PRIORITY_NORMAL
    p = get_priority('some_random_unmapped_type')
    assert p == PRIORITY_NORMAL, f"未知类型应默认 NORMAL，实际 {p}"
    print(f"  ✅ Case 10: 未知 msg_type 默认 NORMAL 优先级")


def main():
    print("=" * 60)
    print("P1-K: MessageBus 优先级 + 背压 + 死信测试")
    print("=" * 60)
    asyncio.run(test_priority_ordering())
    asyncio.run(test_fifo_within_priority())
    asyncio.run(test_backpressure_soft_drops_low())
    asyncio.run(test_backpressure_hard_drops_normal())
    asyncio.run(test_critical_never_dropped())
    asyncio.run(test_dlq_unknown_target())
    asyncio.run(test_metrics_fields())
    asyncio.run(test_backward_compat_broadcast())
    asyncio.run(test_explicit_priority_override())
    asyncio.run(test_unknown_msg_type_defaults_normal())
    print("\n" + "=" * 60)
    print("✅ 全部 10 个测试通过")
    print("=" * 60)


if __name__ == '__main__':
    main()
