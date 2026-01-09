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

    # Расширение коротких/неоднозначных запросов
    QUERY_EXPANSIONS = {
        "ии": "искусственный интеллект OR Machine Learning OR Data Science OR нейросети",
        "ai": "искусственный интеллект OR Machine Learning OR Data Science OR AI engineer",
        "ml": "Machine Learning OR Data Science OR ML engineer OR аналитик данных",
        "машинное обучение": "Machine Learning OR Data Science OR ML engineer",
        "data science": "Data Science OR аналитик данных OR Data Analyst OR Machine Learning",
        "фронтенд": "Frontend OR React OR Vue OR Angular OR фронтенд разработчик",
        "frontend": "Frontend OR React OR Vue OR Angular OR фронтенд разработчик",
        "бэкенд": "Backend OR Python OR Java OR Node.js OR бэкенд разработчик",
        "backend": "Backend OR Python OR Java OR Node.js OR бэкенд разработчик",
        "smm": "SMM OR Social Media OR контент-менеджер OR маркетолог",
        "seo": "SEO OR поисковая оптимизация OR SEO специалист",
        "devops": "DevOps OR SRE OR системный администратор OR Kubernetes OR Docker",
        "qa": "QA OR тестировщик OR Quality Assurance OR тестирование",
        "тестировщик": "QA OR тестировщик OR Quality Assurance OR автоматизация тестирования",
        "аналитик": "аналитик OR бизнес-аналитик OR Data Analyst OR системный аналитик",
        "дизайнер": "дизайнер OR UI/UX OR графический дизайнер OR веб-дизайнер",
        "ux": "UX OR UI/UX OR UX дизайнер OR Product Designer",
        "ui": "UI OR UI/UX OR UI дизайнер OR веб-дизайнер",
        "pm": "Product Manager OR Project Manager OR менеджер продукта OR руководитель проекта",
        "продакт": "Product Manager OR менеджер продукта OR Product Owner",
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

ФОРМИРОВАНИЕ query — используй технические термины как есть (на английском):
- "ИИ/AI" → "Machine Learning OR Data Science OR ML engineer"
- "фронтенд" → "Frontend OR React OR Vue"
- "бэкенд" → "Backend OR Python OR Node.js"
- "питон" → "Python OR Django OR FastAPI"
- "джава" → "Java OR Spring OR Java developer"
- Используй OR для вариантов — увеличивает охват
- НЕ используй общие слова ("работа", "вакансия", "IT", "разработчик" без языка)
""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос. Технические термины на английском. Используй OR. Примеры: 'Python OR Django OR FastAPI', 'Machine Learning OR Data Science', 'Frontend OR React OR Vue', 'Java OR Spring'."
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
