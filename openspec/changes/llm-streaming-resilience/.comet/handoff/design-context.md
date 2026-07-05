# Comet Design Handoff

- Change: llm-streaming-resilience
- Phase: design
- Mode: compact
- Context hash: d81530182b888aa40e6d9d11255f438059fd986b6ddf3d5f7dd91e47833d4515

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/llm-streaming-resilience/proposal.md

- Source: openspec/changes/llm-streaming-resilience/proposal.md
- Lines: 1-26
- SHA256: f6470b757f3c1fd21a670361e332bbcdb2238b379d3a47cdcbdfd2c07117d698

```md
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
```

## openspec/changes/llm-streaming-resilience/design.md

- Source: openspec/changes/llm-streaming-resilience/design.md
- Lines: 1-53
- SHA256: 7d0cf7870ce3d0040019b1b29902d6e9c44dd79b3fe35366e118eec6fdecbdbb

```md
## 架构决策

### 全局限流方案

**选型：类级变量 + asyncio.Lock**

所有 LLMClient 实例共享 `_global_last_call` 时间戳和 `_global_lock`。每次 `chat()` 调用前 acquire lock → 检查距上次调用是否 ≥ 2s → 不足则 sleep → 更新时间戳 → release。

选择 2s 间隔而非 1s：Cloudflare 速率窗口通常按 10req/10s 计算，2s 给出足够余量。

### 流式请求方案

**选型：stream=True + chunk 拼接**

Cloudflare 对流式连接的超时逻辑是"自上一个 chunk 起 N 秒无数据才断"，而非"总连接时间"。只要模型持续产出 token，连接不会被断开。

代价：失去 `response.usage.total_tokens` 统计（流式不返回），改用 `chars` 近似记录。

### 截断检测方案

**选型：finish_reason 检查 + JSON 闭合验证双保险**

1. 流式最后一个 chunk 的 `finish_reason` 字段：
   - `"stop"` = 正常完成
   - `"length"` = max_tokens 用完
   - `None` / 缺失 = 连接异常断开

2. `chat_json()` 层额外检查：raw 文本是否以有效 JSON 结尾（`}` 或 `]`）

任一检测到截断 → 自动重试 1 次（同参数）。重试仍失败 → 正常走降级路径。

### 重试策略

- 仅 chat_json() 层重试（需要结构化输出的场景）
- chat() 层不重试（调用方自行决定）
- 重试次数：1 次（避免雪崩）
- 重试间隔：0（截断是偶发事件，不是持续故障）

## 数据流

```
Agent.ask_claude_json()
  → LLMClient.chat_json()
    → LLMClient.chat()
      → _rate_limit() [全局 Lock, 2s 间隔]
      → client.chat.completions.create(stream=True)
      → 收集 chunks + 记录 finish_reason
      → finish_reason != "stop" → raise StreamTruncatedError
    ← 正常返回 raw text
    → JSON parse
    → 失败 + 截断特征 → 重试 1 次
    → 仍失败 → schema 降级返回 default
```
```

## openspec/changes/llm-streaming-resilience/tasks.md

- Source: openspec/changes/llm-streaming-resilience/tasks.md
- Lines: 1-33
- SHA256: 20a2f17b25bd7f3e07957c4551f451c5760d0ff012192f9397aad7808d11d60e

```md
## 1. 全局限流

- [x] 1.1 LLMClient 类级 `_global_last_call`、`_global_lock`、`_global_min_interval=2.0`
- [x] 1.2 `_rate_limit()` 改为 acquire 全局锁 + sleep + 更新全局时间戳

## 2. 流式请求

- [x] 2.1 `chat()` 改为 `stream=True`，chunk 拼接返回
- [x] 2.2 日志从 tokens 改为 chars 记录

## 3. 截断检测

- [ ] 3.1 定义 `StreamTruncatedError` 异常类
- [ ] 3.2 `chat()` 记录最后一个 chunk 的 `finish_reason`
- [ ] 3.3 `finish_reason` 非 "stop" 且有内容时抛出 StreamTruncatedError

## 4. JSON 重试

- [ ] 4.1 `chat_json()` 捕获 StreamTruncatedError + JSONDecodeError
- [ ] 4.2 截断特征判定（finish_reason 或末尾非 `}`/`]`）
- [ ] 4.3 满足截断 → 重试 1 次同参数调用
- [ ] 4.4 重试仍失败 → 正常 schema 降级

## 5. Prompt 缩减

- [ ] 5.1 SYNTHESIS_PROMPT "筛选出未来4小时最有交易价值的12个" → "最多10个"

## 6. 验证

- [ ] 6.1 单元测试：全局限流跨实例生效
- [ ] 6.2 单元测试：截断检测抛 StreamTruncatedError
- [ ] 6.3 单元测试：chat_json 截断重试成功
- [ ] 6.4 全量 pytest 通过（基线 1484）
```

## openspec/changes/llm-streaming-resilience/specs/llm-stream-resilience/spec.md

- Source: openspec/changes/llm-streaming-resilience/specs/llm-stream-resilience/spec.md
- Lines: 1-24
- SHA256: 1a0c41eb2ee59390fba173e963d09f68cffddb74f5de798dc8f78134bf3e5472

```md
## Requirements

### R1: 全局跨实例限流
- 所有 LLMClient 实例共享同一请求时间戳
- 最小请求间隔 ≥ 2s（可配置）
- 使用 asyncio.Lock 保证并发安全

### R2: 流式请求
- 所有 LLM 调用使用 stream=True
- chunk 拼接为完整文本返回
- 兼容现有 chat()/chat_json() 接口

### R3: 截断检测
- 检测流式 finish_reason：非 "stop" 视为截断
- 截断时抛出 StreamTruncatedError（继承 Exception）

### R4: JSON 重试
- chat_json() 内 JSON 解析失败时检查截断特征
- 截断特征：finish_reason 非 stop，或 raw 文本末尾非 `}` / `]`
- 满足截断特征 → 自动重试 1 次（同参数）
- 重试仍失败 → 正常降级（schema default）

### R5: Prompt 输出压力缩减
- SYNTHESIS_PROMPT 候选标的数从 12 → 10
```

