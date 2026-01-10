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

        # Avito - выполняем ВСЕ запросы для максимального охвата
        # Пагинация внутри парсера обеспечит достаточное количество
        avito_queries = queries[:4]  # До 4 запросов для максимального охвата
        avito_results = []

        print(f"[Search] Running Avito search ({len(avito_queries)} queries)")
        for i, query in enumerate(avito_queries):
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
                print(f"[Search] Avito query {i+1}/{len(avito_queries)}: found {len(result)}")
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

        # Удаляем дубликаты по ID и по названию + компании
        seen_ids = set()
        seen_keys = set()
        unique_vacancies = []
        for v in all_vacancies:
            # Проверяем и по ID, и по ключу (title+company+city)
            if v.id in seen_ids:
                continue
            key = f"{v.title.lower()}_{v.company.lower()}_{v.city.lower()}"
            if key in seen_keys:
                continue
            seen_ids.add(v.id)
            seen_keys.add(key)
            unique_vacancies.append(v)

        print(f"[Search] Found {len(all_vacancies)} total, {len(unique_vacancies)} unique")

        # Балансируем источники перед лимитом (чтобы Avito не терялся)
        max_per_source = self.settings.max_total_vacancies // 3 + 10  # ~35-40 с каждого источника
        by_source = {"hh": [], "avito": [], "superjob": []}
        for v in unique_vacancies:
            if v.source in by_source:
                by_source[v.source].append(v)

        # Сортируем каждый источник по зарплате
        for source in by_source:
            by_source[source].sort(
                key=lambda x: (x.salary_from or 0, x.salary_to or 0),
                reverse=True
            )

        # Берём до max_per_source из каждого источника
        balanced = []
        for source, vacancies in by_source.items():
            taken = vacancies[:max_per_source]
            balanced.extend(taken)
            print(f"[Search] Source {source}: {len(vacancies)} total, took {len(taken)}")

        # Финальная сортировка по зарплате
        balanced.sort(
            key=lambda x: (x.salary_from or 0, x.salary_to or 0),
            reverse=True
        )

        # Ограничиваем количество
        limited = balanced[:self.settings.max_total_vacancies]

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
