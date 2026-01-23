"""
LLM Configuration for CrewAI agents using OpenRouter.
"""
from langchain_openai import ChatOpenAI
from config import get_settings

settings = get_settings()


def get_main_llm() -> ChatOpenAI:
    """
    Основная модель для агентов (Claude Sonnet 4).
    Используется для сложных задач: анализ, стратегия, коммуникация.
    """
    return ChatOpenAI(
        model=settings.model_name,  # anthropic/claude-sonnet-4
        openai_api_key=settings.openrouter_api_key,
        openai_api_base=settings.openrouter_base_url,
        default_headers={
            "HTTP-Referer": "https://jobaisearch.ru",
            "X-Title": "JobAISearch CrewAI",
        },
        temperature=0.7,
        max_tokens=4096,
    )


def get_fast_llm() -> ChatOpenAI:
    """
    Быстрая модель для валидации (Claude Haiku).
    Используется для простых задач: фильтрация, проверка.
    """
    return ChatOpenAI(
        model=settings.validator_model_name,  # anthropic/claude-3-haiku
        openai_api_key=settings.openrouter_api_key,
        openai_api_base=settings.openrouter_base_url,
        default_headers={
            "HTTP-Referer": "https://jobaisearch.ru",
            "X-Title": "JobAISearch CrewAI Validator",
        },
        temperature=0.1,
        max_tokens=2000,
    )


# Pre-configured LLM instances
main_llm = get_main_llm()
fast_llm = get_fast_llm()
