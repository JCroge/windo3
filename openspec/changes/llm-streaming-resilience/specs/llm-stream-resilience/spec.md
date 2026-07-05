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
