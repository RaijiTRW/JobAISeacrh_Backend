"""
Human-like behavior для парсинга
Паттерны задержек и User-Agent ротация
"""

import random
import asyncio
from datetime import datetime, time
from typing import Optional


# User-Agent'ы реальных браузеров
USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    # Firefox Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

# Популярные поисковые запросы для имитации реального поиска
POPULAR_QUERIES = [
    "менеджер",
    "продавец",
    "водитель",
    "курьер",
    "программист",
    "бухгалтер",
    "повар",
    "официант",
    "администратор",
    "кассир",
    "грузчик",
    "охранник",
    "уборщик",
    "разнорабочий",
    "оператор",
    "инженер",
    "дизайнер",
    "маркетолог",
    "юрист",
    "врач",
]

# Популярные города
POPULAR_CITIES = [
    "Москва",
    "Санкт-Петербург",
    "Новосибирск",
    "Екатеринбург",
    "Казань",
    "Нижний Новгород",
    "Челябинск",
    "Самара",
    "Омск",
    "Ростов-на-Дону",
    "Уфа",
    "Красноярск",
    "Воронеж",
    "Пермь",
    "Волгоград",
]


def get_random_user_agent() -> str:
    """Случайный User-Agent"""
    return random.choice(USER_AGENTS)


def get_random_query() -> str:
    """Случайный поисковый запрос"""
    return random.choice(POPULAR_QUERIES)


def get_random_city() -> str:
    """Случайный город"""
    return random.choice(POPULAR_CITIES)


async def human_delay(
    min_seconds: float = 1.0,
    max_seconds: float = 3.0,
    source: Optional[str] = None,
) -> None:
    """
    Человеческая задержка между запросами.
    Avito требует больше времени.
    """
    if source == "avito":
        # Avito агрессивно банит — большие задержки
        delay = random.uniform(30.0, 90.0)
    elif source == "superjob":
        # SuperJob тоже чувствителен
        delay = random.uniform(5.0, 15.0)
    else:
        # HH более толерантен
        delay = random.uniform(min_seconds, max_seconds)

    # Добавляем микровариации как у человека
    delay += random.uniform(-0.5, 0.5)
    delay = max(0.5, delay)  # Минимум 0.5 секунды

    await asyncio.sleep(delay)


async def session_break() -> None:
    """
    Перерыв между сессиями парсинга.
    Имитирует человека, который отвлёкся.
    """
    # 1-5 минут перерыв
    break_time = random.uniform(60, 300)
    print(f"[HumanBehavior] Session break: {break_time:.0f} seconds")
    await asyncio.sleep(break_time)


def is_working_hours() -> bool:
    """
    Проверка рабочих часов (когда люди обычно ищут работу).
    Парсим в основном днём, чтобы выглядеть естественно.
    """
    now = datetime.now()
    current_time = now.time()

    # Рабочие часы: 8:00 - 23:00
    start = time(8, 0)
    end = time(23, 0)

    return start <= current_time <= end


def get_batch_size(source: str) -> int:
    """
    Размер батча для парсинга.
    Маленькие батчи = меньше подозрений.
    """
    if source == "avito":
        return random.randint(5, 10)  # Очень маленькие батчи для Avito
    elif source == "superjob":
        return random.randint(10, 20)
    else:  # hh
        return random.randint(20, 30)


def should_add_noise() -> bool:
    """
    Иногда добавляем "шум" — случайные действия.
    Например, запросы, которые не сохраняем.
    """
    return random.random() < 0.1  # 10% шанс


def get_parse_schedule() -> list[dict]:
    """
    Расписание парсинга на день.
    Имитирует реального пользователя, который ищет работу в разное время.
    """
    # Случайное смещение для каждого дня
    hour_offset = random.randint(-30, 30)  # минуты

    return [
        {"hour": 9, "minute": 0 + hour_offset % 60, "sources": ["hh", "superjob"]},
        {"hour": 12, "minute": 30 + hour_offset % 60, "sources": ["hh", "avito"]},
        {"hour": 15, "minute": 0 + hour_offset % 60, "sources": ["superjob"]},
        {"hour": 18, "minute": 30 + hour_offset % 60, "sources": ["hh", "avito", "superjob"]},
        {"hour": 21, "minute": 0 + hour_offset % 60, "sources": ["hh"]},
    ]


class HumanSession:
    """
    Сессия парсинга с человеческим поведением.
    Отслеживает количество запросов и делает перерывы.
    """

    def __init__(self, source: str):
        self.source = source
        self.request_count = 0
        self.max_requests_before_break = random.randint(5, 15)
        self.user_agent = get_random_user_agent()

    async def before_request(self) -> None:
        """Вызывать перед каждым запросом"""
        self.request_count += 1

        # Задержка между запросами
        await human_delay(source=self.source)

        # Перерыв после N запросов
        if self.request_count >= self.max_requests_before_break:
            await session_break()
            self.request_count = 0
            self.max_requests_before_break = random.randint(5, 15)
            self.user_agent = get_random_user_agent()  # Меняем UA после перерыва

    def get_headers(self) -> dict:
        """Заголовки для запроса"""
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
