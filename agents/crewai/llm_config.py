"""
LLM Client - прямые вызовы OpenRouter API через httpx.
Без зависимости от langchain/crewai.
"""
import httpx
import json
from typing import Optional
from config import get_settings


class LLMClient:
    """Легковесный клиент для OpenRouter API."""

    def __init__(
        self,
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        agent_id: str = None,
    ):
        self.settings = get_settings()
        self.model = model or self.settings.model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = self.settings.openrouter_base_url
        self.agent_id = agent_id  # Для трекинга токенов

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = None,
        max_tokens: int = None,
        json_mode: bool = False,
        agent_id: str = None,
    ) -> str:
        """
        Отправить сообщение в LLM и получить ответ.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        return await self._call(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            agent_id=agent_id,
        )

    async def chat_with_history(
        self,
        system_prompt: str,
        messages: list[dict],
        temperature: float = None,
        max_tokens: int = None,
        json_mode: bool = False,
        agent_id: str = None,
    ) -> str:
        """
        Отправить сообщение с историей.
        """
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)

        return await self._call(
            full_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            agent_id=agent_id,
        )

    async def _call(
        self,
        messages: list[dict],
        temperature: float = None,
        max_tokens: int = None,
        json_mode: bool = False,
        agent_id: str = None,
    ) -> str:
        """Выполнить вызов API."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://jobaisearch.ru",
                    "X-Title": "JobAISearch AI Agents",
                },
                json=payload,
                timeout=60.0,
            )

            if response.status_code != 200:
                print(f"[LLM] Error: {response.status_code} {response.text[:200]}")
                raise Exception(f"LLM API error: {response.status_code}")

            data = response.json()

            # Трекинг токенов
            tracking_id = agent_id or self.agent_id
            if tracking_id:
                usage = data.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                if input_tokens or output_tokens:
                    try:
                        from agents.agents_config import track_usage
                        track_usage(tracking_id, self.model, input_tokens, output_tokens)
                    except Exception as e:
                        print(f"[LLM] Usage tracking error: {e}")

            return data["choices"][0]["message"]["content"]


# Преднастроенные клиенты
def get_main_llm() -> LLMClient:
    """Основная модель (Claude Sonnet) для сложных задач."""
    settings = get_settings()
    return LLMClient(
        model=settings.model_name,
        temperature=settings.crewai_temperature,
        max_tokens=settings.max_tokens,
    )


def get_fast_llm() -> LLMClient:
    """Быстрая модель (Claude Haiku) для валидации."""
    settings = get_settings()
    return LLMClient(
        model=settings.validator_model_name,
        temperature=settings.crewai_validator_temperature,
        max_tokens=2000,
    )
