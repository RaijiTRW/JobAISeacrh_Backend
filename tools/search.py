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

    def __init__(self):
        self.settings = get_settings()
        self.parsers = [
            HHParser(),
            AvitoParser(),
            SuperJobParser(),
        ]

    async def search(self, filters: SearchFilters) -> SearchResult:
        """
        Поиск вакансий по всем источникам
        """
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
            "description": "Поиск вакансий на hh.ru, Avito и SuperJob. Используй когда пользователь хочет найти работу.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос (должность, ключевые слова)"
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
