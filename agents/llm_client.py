"""Claude API 客户端 - 通过 OpenAI 兼容接口调用中转站"""

import os
import time
import json
import asyncio
import httpx
from openai import AsyncOpenAI
from utils.logger import setup_logger


class LLMClient:
    """Claude API 客户端，通过 OpenAI 兼容格式调用中转站"""

    def __init__(self):
        self.logger = setup_logger('llm_client')

        self.api_key = os.getenv('ANTHROPIC_API_KEY')
        self.base_url = os.getenv('ANTHROPIC_BASE_URL', 'https://api.anthropic.com')
        self.model = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-6')

        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY 未配置")

        api_base = self.base_url + '/v1' if not self.base_url.endswith('/v1') else self.base_url
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=api_base,
            default_headers={'User-Agent': 'curl/8.0'},
            timeout=httpx.Timeout(connect=10, read=90, write=10, pool=10),
            max_retries=2,
        )

        self._last_call_time = 0
        self._min_interval = 1.0
        self._call_count = 0

        self.logger.info(f"LLM客户端初始化: model={self.model}, base_url={self.base_url}")

    async def chat(self, system_prompt: str, user_message: str,
                   max_tokens: int = 2000, temperature: float = 0.3) -> str:
        await self._rate_limit()

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ]
            )

            self._call_count += 1
            result = response.choices[0].message.content
            tokens = response.usage.total_tokens if response.usage else 0
            self.logger.info(f"LLM调用成功 (#{self._call_count}, tokens={tokens})")
            return result

        except Exception as e:
            self.logger.error(f"LLM调用失败: {e}")
            raise

    async def chat_json(self, system_prompt: str, user_message: str,
                        max_tokens: int = 2000, temperature: float = 0.2) -> dict:
        system_with_json = system_prompt + "\n\n请以纯JSON格式回复，不要包含markdown代码块。"
        result = await self.chat(system_with_json, user_message, max_tokens, temperature=temperature)

        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[1]
            result = result.rsplit("```", 1)[0]

        return json.loads(result)

    async def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_call_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_call_time = time.time()

    @property
    def stats(self) -> dict:
        return {"total_calls": self._call_count, "model": self.model}
