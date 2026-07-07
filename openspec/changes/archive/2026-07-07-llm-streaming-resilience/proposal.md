## Why

LLM 中转站（api.chivess.com）前置 Cloudflare 代理，导致三类故障：多 Agent 并发突发请求触发速率限制、大 prompt 生成超 60s 被网关断连返回 504、流式模式下连接静默断开致 JSON 截断但不抛异常。系统虽有规则降级兜底，但降级频率过高（13% 失败率）严重影响研判质量。

## What Changes

- 全局跨实例请求限流（类级 asyncio.Lock，2s 最小间隔）— 已实现
- 非流式 → 流式请求，绕过 Cloudflare 60s 网关超时 — 已实现
- 流式 finish_reason 检测：非 "stop" 时抛 StreamTruncatedError
- chat_json() JSON 解析失败自动重试 1 次（截断判定 + 同参数重试）
- SYNTHESIS_PROMPT 候选标的数从 12 → 10，减少输出长度

## Capabilities

### New Capabilities
- `llm-stream-resilience`: 流式调用完整性检测、截断重试、全局限流

### Modified Capabilities
<!-- 无 spec 级别的行为变更 -->

## Impact

- `agents/llm_client.py`: 核心改动文件（全局锁、流式、finish_reason、重试）
- `agents/research/synthesizer.py`: prompt 文案调整（12→10）
- 所有 LLM 调用方受益，无接口变更
- 降级频率预期从 13% → <2%
