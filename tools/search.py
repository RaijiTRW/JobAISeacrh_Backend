"""
Единый интерфейс поиска вакансий
Агрегирует результаты из всех источников
"""

import asyncio
from typing import Optional
from models.vacancy import Vacancy, SearchFilters, SearchResult
from tools.parsers.hh import HHParser
from tools.parsers.avito import AvitoParser
from tools.parsers.superjob import SuperJobParser
from config import get_settings


class VacancySearchTool:
    """Единый инструмент поиска вакансий"""

    # Расширение коротких/неоднозначных запросов (НА РУССКОМ для российских сайтов)
    QUERY_EXPANSIONS = {
        "ии": "машинное обучение OR data scientist OR нейросети OR искусственный интеллект",
        "ai": "машинное обучение OR data scientist OR нейросети",
        "ml": "машинное обучение OR data scientist OR аналитик данных",
        "машинное обучение": "машинное обучение OR data scientist OR ML разработчик",
        "data science": "аналитик данных OR data scientist OR машинное обучение",
        "фронтенд": "фронтенд разработчик OR React OR Vue OR Angular",
        "frontend": "фронтенд разработчик OR React OR Vue OR Angular",
        "фронт": "фронтенд разработчик OR React OR Vue",
        "бэкенд": "бэкенд разработчик OR Python разработчик OR Java разработчик",
        "backend": "бэкенд разработчик OR Python разработчик OR серверная разработка",
        "бэк": "бэкенд разработчик OR Python разработчик",
        "smm": "SMM менеджер OR контент-менеджер OR маркетолог",
        "seo": "SEO специалист OR поисковая оптимизация",
        "devops": "DevOps инженер OR системный администратор OR SRE",
        "qa": "тестировщик OR QA инженер OR тестирование",
        "тестировщик": "тестировщик OR QA инженер OR автоматизация тестирования",
        "аналитик": "аналитик OR бизнес-аналитик OR аналитик данных OR системный аналитик",
        "дизайнер": "дизайнер OR UI/UX дизайнер OR графический дизайнер OR веб-дизайнер",
        "ux": "UX дизайнер OR UI/UX дизайнер OR продуктовый дизайнер",
        "ui": "UI дизайнер OR UI/UX дизайнер OR веб-дизайнер",
        "pm": "менеджер продукта OR менеджер проекта OR продакт-менеджер",
        "продакт": "менеджер продукта OR продакт-менеджер OR product owner",
    }

    def __init__(self):
        self.settings = get_settings()
        self.parsers = [
            HHParser(),
            AvitoParser(),
            SuperJobParser(),
        ]

    def _expand_query(self, query: str) -> str:
        """Расширяет короткие/неоднозначные запросы"""
        query_lower = query.lower().strip()

        # Проверяем точное совпадение
        if query_lower in self.QUERY_EXPANSIONS:
            expanded = self.QUERY_EXPANSIONS[query_lower]
            print(f"[Search] Expanded query '{query}' -> '{expanded}'")
            return expanded

        # Если запрос слишком короткий (менее 3 символов) — не расширяем, вернём как есть
        if len(query_lower) < 3:
            print(f"[Search] Query '{query}' too short, using as-is")
            return query

        return query

    async def search(self, filters: SearchFilters) -> SearchResult:
        """
        Поиск вакансий по всем источникам
        """
        # Расширяем запрос если нужно
        original_query = filters.query
        filters.query = self._expand_query(filters.query)
        print(f"[Search] Searching for: '{filters.query}' (original: '{original_query}')")

        # Запускаем все парсеры параллельно
        tasks = [
            parser.search(filters, limit=self.settings.max_vacancies_per_source)
            for parser in self.parsers
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Собираем все вакансии
        all_vacancies: list[Vacancy] = []
        for result in results:
            if isinstance(result, list):
                all_vacancies.extend(result)
            elif isinstance(result, Exception):
                print(f"Parser error: {result}")

        # Удаляем дубликаты по названию + компании
        seen = set()
        unique_vacancies = []
        for v in all_vacancies:
            key = f"{v.title.lower()}_{v.company.lower()}_{v.city.lower()}"
            if key not in seen:
                seen.add(key)
                unique_vacancies.append(v)

        # Сортируем по зарплате (сначала с указанной)
        unique_vacancies.sort(
            key=lambda x: (x.salary_from or 0, x.salary_to or 0),
            reverse=True
        )

        # Ограничиваем количество
        limited = unique_vacancies[:self.settings.max_total_vacancies]

        return SearchResult(
            vacancies=limited,
            total_found=len(unique_vacancies),
            filters_applied=filters,
        )

    def get_tool_definition(self) -> dict:
        """
        Определение инструмента для Claude
        """
        return {
            "name": "search_vacancies",
            "description": """Поиск вакансий на hh.ru, Avito и SuperJob.

ВАЖНО: Запросы НА РУССКОМ! Мы ищем на российских сайтах.
Названия языков (Python, Java, React) можно оставлять как есть.

Примеры query:
- "ИИ/AI" → "машинное обучение OR data scientist OR нейросети"
- "фронтенд" → "фронтенд разработчик OR React OR Vue"
- "бэкенд" → "бэкенд разработчик OR Python разработчик"
- "питон" → "Python разработчик OR Django"
- "джава" → "Java разработчик OR Spring"
- Используй OR для вариантов
""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос НА РУССКОМ. Названия языков можно оставлять (Python, React). Примеры: 'Python разработчик OR Django', 'машинное обучение OR data scientist', 'фронтенд разработчик OR React'."
                    },
                    "city": {
                        "type": "string",
                        "description": "Город для поиска"
                    },
                    "salary_from": {
                        "type": "integer",
                        "description": "Минимальная зарплата"
                    },
                    "salary_to": {
                        "type": "integer",
                        "description": "Максимальная зарплата"
                    },
                    "experience": {
                        "type": "string",
                        "enum": ["no_experience", "1-3", "3-6", "6+"],
                        "description": "Требуемый опыт работы"
                    },
                    "employment_type": {
                        "type": "string",
                        "enum": ["full", "part", "remote"],
                        "description": "Тип занятости"
                    },
                    "exclude_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Слова для исключения из результатов"
                    }
                },
                "required": ["query", "city"]
            }
        }


# Singleton
vacancy_search = VacancySearchTool()
