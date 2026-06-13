# Tasks

## 1. bot LLM 配置改读独立变量名 (bot-llm-config-isolation)
- [x] 1.1 `agents/llm_client.py`：`os.getenv('ANTHROPIC_API_KEY')`→`BOT_LLM_API_KEY`、`ANTHROPIC_BASE_URL`→`BOT_LLM_BASE_URL`、`ANTHROPIC_MODEL`→`BOT_LLM_MODEL`（默认值不变：base=https://api.anthropic.com, model=claude-opus-4-6）；行 204 报错文案 `ANTHROPIC_API_KEY 未配置`→`BOT_LLM_API_KEY 未配置`。**不**加 ANTHROPIC_* 回退
- [x] 1.2 `.env.example`：3 个键 `ANTHROPIC_*`→`BOT_LLM_*`（含注释说明：bot 专用，与 Claude Code 的 ANTHROPIC_* 隔离）
- [x] 1.3 `.env`（gitignore，不进 git）：改成 `BOT_LLM_API_KEY=<新key>` / `BOT_LLM_BASE_URL=http://156.238.228.230:8080` / `BOT_LLM_MODEL=claude-opus-4-6`，删旧 ANTHROPIC_* LLM 行
- [x] 1.4 单测 `test_bot_llm_env_isolation.py`：设 `BOT_LLM_MODEL=X` 且 `ANTHROPIC_MODEL=Y` → LLMClient.model==X（忽略 ANTHROPIC_*）；BOT_LLM_* 全设时 key/base 正确；全 unset 时走默认 + unavailable

## 2. 验证与收尾
- [x] 2.1 全量 `python3 -m pytest -q` 通过（基线 + 新增）
- [x] 2.2 真实验证：load .env → LLMClient 读到 `claude-opus-4-6` 且不受 shell 里 `ANTHROPIC_MODEL=claude-opus-4-8` 影响；一次 chat 成功
- [x] 2.3 编译 `python3 -m compileall -q agents/llm_client.py`
