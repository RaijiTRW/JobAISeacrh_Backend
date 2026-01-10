from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # OpenRouter API
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Supabase
    supabase_url: str = ""
    supabase_key: str = ""

    # App settings
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    # AI Settings
    model_name: str = "anthropic/claude-sonnet-4"
    validator_model_name: str = "anthropic/claude-3-haiku"  # Быстрая модель для валидации
    max_tokens: int = 4096

    # Search settings
    max_vacancies_per_source: int = 40  # Больше с каждого источника
    max_total_vacancies: int = 80  # Больше общий лимит для максимального охвата

    # Proxy settings (для обхода rate limiting на Avito)
    # Формат: http://user:pass@host:port или http://host:port
    # Можно указать несколько через запятую для ротации
    proxy_urls: str = ""  # Пустая строка = прокси отключены

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
