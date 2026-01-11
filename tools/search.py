"""
Единый интерфейс поиска вакансий
Сначала ищем в БД, если мало — дополняем live-парсингом
"""

import asyncio
from typing import Optional
from models.vacancy import Vacancy, SearchFilters, SearchResult
from tools.parsers.hh import HHParser
from tools.parsers.avito import AvitoParser
from tools.parsers.superjob import SuperJobParser
from services.vacancy_storage import vacancy_storage_service
from config import get_settings


class VacancySearchTool:
    """Единый инструмент поиска вакансий"""

    # Минимальное количество вакансий из БД, после которого не нужен live-поиск
    MIN_DB_RESULTS = 10

    def __init__(self):
        self.settings = get_settings()
        self.parsers = [
            HHParser(),
            AvitoParser(),
            SuperJobParser(),
        ]

    async def search(self, filters: SearchFilters) -> SearchResult:
        """
        Поиск вакансий: сначала БД, потом live-парсинг если мало.
        """
        queries = filters.queries
        print(f"[Search] Searching with {len(queries)} queries: {queries}")

        # === ШАГ 1: Поиск в БД ===
        db_vacancies = await self._search_db(filters)
        print(f"[Search] Found {len(db_vacancies)} in DB")

        # Если достаточно результатов из БД — возвращаем их
        if len(db_vacancies) >= self.MIN_DB_RESULTS:
            print(f"[Search] Enough results from DB, skipping live search")
            # Применяем дедупликацию и лимиты
            processed = self._process_results(db_vacancies, filters)
            return SearchResult(
                vacancies=processed,
                total_found=len(db_vacancies),
                filters_applied=filters,
            )

        # === ШАГ 2: Live-парсинг (если БД недостаточно) ===
        print(f"[Search] Not enough in DB ({len(db_vacancies)}), starting live search...")
        live_vacancies = await self._search_live(filters)
        print(f"[Search] Found {len(live_vacancies)} from live search")

        # Сохраняем новые вакансии в БД (в фоне)
        asyncio.create_task(self._save_to_db(live_vacancies))

        # Объединяем: БД + live (без дубликатов)
        all_vacancies = self._merge_results(db_vacancies, live_vacancies)
        print(f"[Search] Total after merge: {len(all_vacancies)}")

        # Применяем дедупликацию и лимиты
        processed = self._process_results(all_vacancies, filters)

        return SearchResult(
            vacancies=processed,
            total_found=len(all_vacancies),
            filters_applied=filters,
        )

    async def _search_db(self, filters: SearchFilters) -> list[Vacancy]:
        """Поиск в базе данных"""
        all_vacancies = []

        for query in filters.queries[:5]:  # Максимум 5 запросов к БД
            try:
                stored, _ = await vacancy_storage_service.search_vacancies(
                    query=query,
                    city=filters.city,
                    salary_from=filters.salary_from,
                    experience=filters.experience,
                    limit=30,
                    offset=0,
                )
                for sv in stored:
                    vacancy = vacancy_storage_service.to_vacancy(sv)
                    all_vacancies.append(vacancy)
            except Exception as e:
                print(f"[Search] DB search error for '{query}': {e}")

        # Дедупликация по ID
        seen_ids = set()
        unique = []
        for v in all_vacancies:
            if v.id not in seen_ids:
                seen_ids.add(v.id)
                unique.append(v)

        return unique

    async def _search_live(self, filters: SearchFilters) -> list[Vacancy]:
        """Live-парсинг с сайтов"""
        queries = filters.queries

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

        # Avito - максимум 2 запроса для скорости
        avito_queries = queries[:2]
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

        return all_vacancies

    def _merge_results(self, db_vacancies: list[Vacancy], live_vacancies: list[Vacancy]) -> list[Vacancy]:
        """Объединить результаты из БД и live-поиска без дубликатов"""
        seen_ids = {v.id for v in db_vacancies}
        merged = list(db_vacancies)

        for v in live_vacancies:
            if v.id not in seen_ids:
                seen_ids.add(v.id)
                merged.append(v)

        return merged

    async def _save_to_db(self, vacancies: list[Vacancy]) -> None:
        """Сохранить вакансии в БД (в фоне)"""
        saved = 0
        for vacancy in vacancies:
            try:
                if await vacancy_storage_service.save_vacancy(vacancy):
                    saved += 1
            except Exception as e:
                print(f"[Search] Save to DB error: {e}")
        print(f"[Search] Saved {saved}/{len(vacancies)} vacancies to DB")

    def _process_results(self, vacancies: list[Vacancy], filters: SearchFilters) -> list[Vacancy]:
        """Дедупликация, балансировка и лимиты"""
        # Дедупликация по ID и ключу
        seen_ids = set()
        seen_keys = set()
        unique_vacancies = []
        for v in vacancies:
            if v.id in seen_ids:
                continue
            key = f"{v.title.lower()}_{v.company.lower()}_{v.city.lower()}"
            if key in seen_keys:
                continue
            seen_ids.add(v.id)
            seen_keys.add(key)
            unique_vacancies.append(v)

        print(f"[Search] After ID/key dedupe: {len(unique_vacancies)}")

        # Семантическая дедупликация
        unique_vacancies = self._semantic_dedupe(unique_vacancies)
        print(f"[Search] After semantic dedupe: {len(unique_vacancies)}")

        # Балансируем источники
        max_per_source = self.settings.max_total_vacancies // 3 + 5
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
        for source, source_vacancies in by_source.items():
            taken = source_vacancies[:max_per_source]
            balanced.extend(taken)
            print(f"[Search] Source {source}: {len(source_vacancies)} total, took {len(taken)}")

        # Финальная сортировка по зарплате
        balanced.sort(
            key=lambda x: (x.salary_from or 0, x.salary_to or 0),
            reverse=True
        )

        # Ограничиваем количество
        return balanced[:self.settings.max_total_vacancies]

    def _semantic_dedupe(self, vacancies: list[Vacancy]) -> list[Vacancy]:
        """
        Семантическая дедупликация — группируем похожие вакансии.
        "Оператор ПВЗ" ≈ "Менеджер пункта выдачи" ≈ "Сотрудник ПВЗ"
        Оставляем лучшую из группы (выше зарплата, лучше описание).
        """
        if len(vacancies) <= 1:
            return vacancies

        # Нормализация названия для сравнения
        def normalize(title: str) -> str:
            t = title.lower().strip()
            # Убираем стоп-слова которые не влияют на смысл
            for word in ["требуется", "нужен", "нужна", "ищем", "вакансия", "работа"]:
                t = t.replace(word, "")
            return t.strip()

        # Синонимы для группировки
        synonyms = {
            "пвз": ["пункт выдачи", "выдача заказов", "пункт выдачи заказов"],
            "менеджер": ["оператор", "сотрудник", "администратор", "специалист"],
            "разработчик": ["developer", "программист", "инженер"],
            "frontend": ["фронтенд", "front-end", "фронт"],
            "backend": ["бэкенд", "back-end", "бэк"],
        }

        def get_base_key(title: str) -> str:
            """Получить базовый ключ для группировки"""
            t = normalize(title)

            # Заменяем синонимы на базовую форму
            for base, syns in synonyms.items():
                for syn in syns:
                    if syn in t:
                        t = t.replace(syn, base)
                if base in t:
                    pass  # уже есть

            # Извлекаем ключевые слова (первые 3 значимых слова)
            words = [w for w in t.split() if len(w) > 2][:3]
            return " ".join(sorted(words))

        # Группируем по базовому ключу + город
        groups: dict[str, list[Vacancy]] = {}
        for v in vacancies:
            key = f"{get_base_key(v.title)}_{v.city.lower()}"
            if key not in groups:
                groups[key] = []
            groups[key].append(v)

        # Из каждой группы берём лучшую вакансию
        result = []
        for key, group in groups.items():
            if len(group) == 1:
                result.append(group[0])
            else:
                # Сортируем: выше зарплата, длиннее описание, hh > avito > superjob
                source_priority = {"hh": 3, "avito": 2, "superjob": 1}
                group.sort(
                    key=lambda x: (
                        x.salary_from or 0,
                        x.salary_to or 0,
                        len(x.description or ""),
                        source_priority.get(x.source, 0),
                    ),
                    reverse=True
                )
                best = group[0]
                result.append(best)

                if len(group) > 1:
                    print(f"[Search] Dedupe group '{key[:30]}': {len(group)} → 1 (kept: {best.title[:40]})")

        return result

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
