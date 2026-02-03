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

    # Минимальное количество вакансий из БД, после которого можно пропустить live-поиск
    # (но только если есть все источники)
    MIN_DB_RESULTS = 20

    def __init__(self):
        self.settings = get_settings()
        self.parsers = [
            HHParser(),
            AvitoParser(),
            SuperJobParser(),
        ]

    async def search(self, filters: SearchFilters) -> SearchResult:
        """
        Поиск вакансий: сначала БД, потом live-парсинг.
        Учитывает режим поиска: search_in_feed (БД) и search_online (live).
        """
        queries = filters.queries
        search_in_feed = filters.search_in_feed
        search_online = filters.search_online

        print(f"[Search] Searching with {len(queries)} queries: {queries}")
        print(f"[Search] Mode: feed={search_in_feed}, online={search_online}")

        db_vacancies = []
        live_vacancies = []

        # === ШАГ 1: Поиск в БД (если включен) ===
        if search_in_feed:
            db_vacancies = await self._search_db(filters)
            print(f"[Search] Found {len(db_vacancies)} in DB")

        # === ШАГ 2: Live-парсинг (если включен) ===
        if search_online:
            # Если только online — ищем всё
            # Если оба включены — сначала проверяем какие источники отсутствуют в БД
            if search_in_feed and db_vacancies:
                db_sources = set(v.source for v in db_vacancies)
                missing_sources = {"hh", "avito", "superjob"} - db_sources

                # Если достаточно результатов И есть все источники — пропускаем live
                if len(db_vacancies) >= self.MIN_DB_RESULTS and not missing_sources:
                    print(f"[Search] Enough results from DB with all sources, skipping live search")
                else:
                    if missing_sources:
                        print(f"[Search] Missing sources in DB: {missing_sources}, doing live search for them")
                        live_vacancies = await self._search_live(filters, only_sources=missing_sources)
                    else:
                        print(f"[Search] Not enough in DB ({len(db_vacancies)}), starting full live search...")
                        live_vacancies = await self._search_live(filters)
            else:
                # Только online или БД пуста — полный live поиск
                print(f"[Search] Doing full live search...")
                live_vacancies = await self._search_live(filters)

            print(f"[Search] Found {len(live_vacancies)} from live search")

            # Сохраняем новые вакансии в БД (в фоне)
            if live_vacancies:
                asyncio.create_task(self._save_to_db(live_vacancies))

        # Объединяем результаты
        if search_in_feed and search_online:
            all_vacancies = self._merge_results(db_vacancies, live_vacancies)
        elif search_in_feed:
            all_vacancies = db_vacancies
        else:
            all_vacancies = live_vacancies

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

        for query in filters.queries[:7]:  # Максимум 7 запросов к БД
            try:
                stored, _ = await vacancy_storage_service.search_vacancies(
                    query=query,
                    city=filters.city,
                    salary_from=filters.salary_from,
                    experience=filters.experience,
                    limit=50,
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

    async def _search_live_fast(self, filters: SearchFilters) -> list[Vacancy]:
        """Быстрый поиск — только HH + SuperJob (БЕЗ Avito)"""
        queries = filters.queries

        hh_parser = self.parsers[0]  # HHParser
        sj_parser = self.parsers[2]  # SuperJobParser

        # HH и SuperJob параллельно
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

        print(f"[Search] Fast search: HH + SuperJob ({len(parallel_tasks)} tasks)")
        parallel_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)

        # Собираем результаты
        all_vacancies: list[Vacancy] = []
        for result in parallel_results:
            if isinstance(result, list):
                all_vacancies.extend(result)
            elif isinstance(result, Exception):
                print(f"[Search] Fast parser error: {result}")

        return all_vacancies

    async def _search_live(self, filters: SearchFilters, only_sources: set[str] = None) -> list[Vacancy]:
        """Live-парсинг с сайтов. only_sources - если указан, парсим только эти источники."""
        queries = filters.queries

        hh_parser = self.parsers[0]  # HHParser
        avito_parser = self.parsers[1]  # AvitoParser
        sj_parser = self.parsers[2]  # SuperJobParser

        # Определяем какие источники парсить
        search_hh = only_sources is None or "hh" in only_sources
        search_sj = only_sources is None or "superjob" in only_sources
        search_avito = only_sources is None or "avito" in only_sources

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
            if search_hh:
                parallel_tasks.append(hh_parser.search(single_filter, limit=self.settings.max_vacancies_per_source))
            if search_sj:
                parallel_tasks.append(sj_parser.search(single_filter, limit=self.settings.max_vacancies_per_source))

        sources_str = []
        if search_hh:
            sources_str.append("HH")
        if search_sj:
            sources_str.append("SuperJob")

        if parallel_tasks:
            print(f"[Search] Running {len(parallel_tasks)} parallel tasks ({' + '.join(sources_str)})")
            parallel_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)
        else:
            parallel_results = []

        # Avito - максимум 3 запроса
        avito_results = []
        if search_avito:
            avito_queries = queries[:3]
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
        print(f"[Search] _process_results: received {len(vacancies)} vacancies")

        # Исключаем вакансии по ID (для функции "Еще")
        if filters.exclude_vacancy_ids:
            exclude_set = set(filters.exclude_vacancy_ids)
            vacancies = [v for v in vacancies if v.id not in exclude_set]
            print(f"[Search] After excluding {len(filters.exclude_vacancy_ids)} IDs: {len(vacancies)}")

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

        # Debug: check source values
        if unique_vacancies:
            sources_set = set(v.source for v in unique_vacancies)
            print(f"[Search] Unique sources found: {sources_set}")

        # Балансируем источники (берём больше с каждого)
        max_per_source = self.settings.max_total_vacancies // 2
        by_source = {"hh": [], "avito": [], "superjob": []}
        for v in unique_vacancies:
            # Нормализуем source для проверки (hh.ru -> hh, superjob -> superjob)
            source_normalized = v.source.lower().replace(".ru", "").replace(".", "").replace("_", "")
            # hh_ или hh.ru -> hh
            if "hh" in source_normalized:
                by_source["hh"].append(v)
            elif "avito" in source_normalized:
                by_source["avito"].append(v)
            elif "superjob" in source_normalized or "sj" in source_normalized:
                by_source["superjob"].append(v)
            elif v.source in by_source:
                by_source[v.source].append(v)
            else:
                # Неизвестный source - добавляем в hh для сохранения вакансии
                print(f"[Search] Unknown source '{v.source}', adding to hh bucket")
                by_source["hh"].append(v)

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
        print(f"[Search] _semantic_dedupe: received {len(vacancies)} vacancies")

        if len(vacancies) <= 1:
            print(f"[Search] _semantic_dedupe: too few vacancies, skipping")
            return vacancies

        # Нормализация названия для сравнения
        def normalize(title: str) -> str:
            t = str(title).lower().strip() if title else ""
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

            # Фолбэк: если слов мало, используем все слова длиной > 1
            if not words:
                words = [w for w in t.split() if len(w) > 1][:3]

            key = " ".join(sorted(words))

            # Последний фолбэк: используем первые 3 символа заголовка
            if not key:
                key = title[:3].lower() if title else "_empty_"

            return key

        # Группируем по базовому ключу + город
        groups: dict[str, list[Vacancy]] = {}
        for v in vacancies:
            title_key = get_base_key(v.title)
            city_key = str(v.city).lower() if v.city else "_unknown_"
            key = f"{title_key}_{city_key}"
            if key not in groups:
                groups[key] = []
            groups[key].append(v)

        print(f"[Search] _semantic_dedupe: created {len(groups)} groups from {len(vacancies)} vacancies")
        if len(groups) < 10:
            for key, group in list(groups.items())[:5]:
                print(f"[Search]   Group '{key[:40]}': {len(group)} vacancies")

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

        print(f"[Search] _semantic_dedupe: returning {len(result)} vacancies")
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
