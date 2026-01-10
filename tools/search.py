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
        Поиск вакансий по всем источникам.
        HH: все queries параллельно
        Avito: 3 queries ПОСЛЕДОВАТЕЛЬНО (rate limiting)
        SuperJob: все queries параллельно
        """
        queries = filters.queries
        print(f"[Search] Searching with {len(queries)} queries: {queries}")

        hh_parser = self.parsers[0]  # HHParser
        avito_parser = self.parsers[1]  # AvitoParser
        sj_parser = self.parsers[2]  # SuperJobParser

        # HH и SuperJob - все queries параллельно
        parallel_tasks = []
        for query in queries:
            single_filter = SearchFilters(
                query=query,
                city=filters.city,
                salary_from=filters.salary_from,
                salary_to=filters.salary_to,
                experience=filters.experience,
                employment_type=filters.employment_type,
                exclude_keywords=filters.exclude_keywords,
            )
            parallel_tasks.append(hh_parser.search(single_filter, limit=self.settings.max_vacancies_per_source))
            parallel_tasks.append(sj_parser.search(single_filter, limit=self.settings.max_vacancies_per_source))

        print(f"[Search] Running {len(parallel_tasks)} parallel tasks (HH + SuperJob)")
        parallel_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)

        # Avito - АДАПТИВНЫЙ поиск: если первый запрос нашёл достаточно — не делаем лишних
        # Это экономит запросы и снижает шанс 429 ошибки
        avito_queries = queries[:3]  # Максимум 3 запроса
        avito_results = []
        avito_total_found = 0
        min_target = 15  # Минимум вакансий для остановки

        print(f"[Search] Running adaptive Avito search (up to {len(avito_queries)} queries)")
        for i, query in enumerate(avito_queries):
            # Если уже нашли достаточно — не делаем лишних запросов
            if avito_total_found >= min_target:
                print(f"[Search] Avito: already found {avito_total_found}, skipping remaining queries")
                break

            avito_filter = SearchFilters(
                query=query,
                city=filters.city,
                salary_from=filters.salary_from,
                salary_to=filters.salary_to,
                experience=filters.experience,
                employment_type=filters.employment_type,
                exclude_keywords=filters.exclude_keywords,
            )
            try:
                result = await avito_parser.search(avito_filter, limit=self.settings.max_vacancies_per_source)
                avito_results.append(result)
                avito_total_found += len(result)
                print(f"[Search] Avito query {i+1}/{len(avito_queries)}: found {len(result)}, total: {avito_total_found}")
            except Exception as e:
                print(f"[Search] Avito error: {e}")
                avito_results.append([])

        # Объединяем все результаты
        results = list(parallel_results) + avito_results

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

        print(f"[Search] Found {len(all_vacancies)} total, {len(unique_vacancies)} unique")

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

ВАЖНО: Генерируй 3-6 РАЗНЫХ вариантов запроса в queries для максимального охвата!

Примеры queries:
- "ПВЗ" → queries: ["пункт выдачи заказов", "менеджер пвз", "оператор пвз", "сотрудник пвз", "пункт выдачи Wildberries"]
- "вайлдберриз" → queries: ["пункт выдачи Wildberries", "менеджер Wildberries", "WB пункт выдачи"]
- "фронтенд" → queries: ["фронтенд разработчик", "frontend developer", "React разработчик", "Vue разработчик"]
- "питон" → queries: ["Python разработчик", "Python developer", "Django", "FastAPI"]
- "ИИ" → queries: ["машинное обучение", "data scientist", "ML инженер", "нейросети"]

КРИТИЧНО для ПВЗ: НЕ используй просто "Wildberries"/"Ozon" — добавляй "пункт выдачи"!
НЕ добавляй: логист, сборщик, курьер, водитель — если не просили!
""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Список вариантов поисковых запросов (3-6 штук). Генерируй синонимы и альтернативные названия профессии!",
                        "minItems": 1,
                        "maxItems": 6
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
                "required": ["queries", "city"]
            }
        }


# Singleton
vacancy_search = VacancySearchTool()
