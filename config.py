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
    model_name: str = "anthropic/claude-sonnet-4-5-20250514"
    max_tokens: int = 4096

    # Search settings
    max_vacancies_per_source: int = 20
    max_total_vacancies: int = 50

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
