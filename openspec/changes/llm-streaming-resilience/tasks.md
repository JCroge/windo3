## 1. 全局限流

- [x] 1.1 LLMClient 类级 `_global_last_call`、`_global_lock`、`_global_min_interval=2.0`
- [x] 1.2 `_rate_limit()` 改为 acquire 全局锁 + sleep + 更新全局时间戳

## 2. 流式请求

- [x] 2.1 `chat()` 改为 `stream=True`，chunk 拼接返回
- [x] 2.2 日志从 tokens 改为 chars 记录

## 3. 截断检测

- [x] 3.1 定义 `StreamTruncatedError` 异常类
- [x] 3.2 `chat()` 记录最后一个 chunk 的 `finish_reason`
- [x] 3.3 `finish_reason` 非 "stop" 且有内容时抛出 StreamTruncatedError

## 4. JSON 重试

- [x] 4.1 `chat_json()` 捕获 StreamTruncatedError + JSONDecodeError
- [x] 4.2 截断特征判定（finish_reason 或末尾非 `}`/`]`）
- [x] 4.3 满足截断 → 重试 1 次同参数调用
- [x] 4.4 重试仍失败 → 正常 schema 降级

## 5. Prompt 缩减

- [x] 5.1 SYNTHESIS_PROMPT "筛选出未来4小时最有交易价值的12个" → "最多10个"

## 6. 验证

- [x] 6.1 单元测试：全局限流跨实例生效
- [x] 6.2 单元测试：截断检测抛 StreamTruncatedError
- [x] 6.3 单元测试：chat_json 截断重试成功
- [x] 6.4 全量 pytest 通过（1490 passed，基线未退化）
