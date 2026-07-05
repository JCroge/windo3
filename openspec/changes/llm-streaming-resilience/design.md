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
