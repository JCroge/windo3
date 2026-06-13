from agents.llm_client import LLMClient


def test_uses_bot_llm_model_not_anthropic(monkeypatch):
    monkeypatch.setenv("BOT_LLM_MODEL", "bot-model-x")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-8")  # CLI 的，应被忽略
    monkeypatch.setenv("BOT_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("BOT_LLM_BASE_URL", "http://example")
    c = LLMClient()
    assert c.model == "bot-model-x"          # 读 BOT_LLM_MODEL
    assert c.model != "claude-opus-4-8"       # 不读 ANTHROPIC_MODEL


def test_reads_dedicated_key_and_base(monkeypatch):
    monkeypatch.setenv("BOT_LLM_API_KEY", "sk-bot-123")
    monkeypatch.setenv("BOT_LLM_BASE_URL", "http://my-proxy:9000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-cli-should-be-ignored")
    monkeypatch.delenv("BOT_LLM_MODEL", raising=False)
    c = LLMClient()
    assert c.api_key == "sk-bot-123"
    assert c.base_url == "http://my-proxy:9000"
    assert c.model == "claude-opus-4-6"        # 默认值不变


def test_defaults_when_unset(monkeypatch):
    for k in ("BOT_LLM_API_KEY", "BOT_LLM_BASE_URL", "BOT_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-cli")  # 即便 CLI 有 key，bot 也不该用
    c = LLMClient()
    assert c.base_url == "https://api.anthropic.com"
    assert c.model == "claude-opus-4-6"
    assert c.available is False                # 无 BOT_LLM_API_KEY → 不可用 → 规则降级
