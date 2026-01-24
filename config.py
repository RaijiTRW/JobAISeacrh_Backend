from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # OpenRouter API
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Supabase
    supabase_url: str = ""
    supabase_key: str = ""  # anon key (for client-side, subject to RLS)
    supabase_service_key: str = ""  # service role key (bypasses RLS, for admin operations)

    # App settings
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    # AI Settings
    model_name: str = "anthropic/claude-sonnet-4"
    validator_model_name: str = "anthropic/claude-3-haiku"  # Быстрая модель для валидации
    max_tokens: int = 4096

    # CrewAI Settings
    crewai_verbose: bool = False  # Подробный вывод агентов
    crewai_max_iterations: int = 10  # Максимум итераций для агента
    crewai_temperature: float = 0.7  # Температура для основной модели
    crewai_validator_temperature: float = 0.1  # Температура для валидатора (более детерминированный)

    # Search settings
    max_vacancies_per_source: int = 200  # Вакансий с каждого источника (HH, SuperJob)
    max_total_vacancies: int = 500  # Минимум 500 вакансий для пользователя

    # Proxy settings (для обхода rate limiting на Avito)
    # Формат: http://user:pass@host:port или http://host:port
    # Можно указать несколько через запятую для ротации
    proxy_urls: str = ""  # Пустая строка = прокси отключены

    # YooKassa настройки
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_return_url: str = "http://localhost:3000/subscription/success"

    # Подписки
    subscription_price: int = 799  # рублей в месяц
    extra_requests_price: int = 99  # рублей за пакет
    extra_requests_count: int = 10  # запросов в пакете

    # Pro Trial (при регистрации)
    pro_trial_days: int = 7  # 7 дней Pro Trial
    pro_trial_daily_limit: int = 15  # 15 запросов/день

    # Base (бесплатный план навсегда)
    base_daily_limit: int = 3  # 3 запроса/день, только лента

    # Pro (платная подписка)
    pro_daily_limit: int = 15  # 15 запросов/день (было 10)

    # Массовый парсинг (scheduler)
    mass_parsing_requests: int = 50  # запросов за сеанс
    hh_mass_batch_size: int = 100  # HH API поддерживает до 100
    superjob_mass_batch_size: int = 30  # SuperJob ограничен web scraping
    parsing_min_delay: float = 2.0  # мин. задержка между запросами (сек)
    parsing_max_delay: float = 8.0  # макс. задержка между запросами (сек)
    micro_break_every: int = 10  # микропауза каждые N запросов
    micro_break_min: float = 30.0  # мин. микропауза (сек)
    micro_break_max: float = 60.0  # макс. микропауза (сек)

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
