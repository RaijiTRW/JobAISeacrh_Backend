"""
Сервис для ленты вакансий
Чтение из БД + fallback на live парсинг
"""
import asyncio
from datetime import datetime
from typing import Optional

from models.vacancy import Vacancy, SearchFilters
from models.feed import FeedFilters, FeedResult
from tools.parsers.hh import HHParser
from tools.parsers.avito import AvitoParser
from tools.parsers.superjob import SuperJobParser
from services.vacancy_storage import vacancy_storage_service
from config import get_settings


class VacancyFeedService:
    """Сервис для ленты вакансий (читает из БД)"""

    def __init__(self):
        self.settings = get_settings()
        self.hh_parser = HHParser()
        self.avito_parser = AvitoParser()
        self.sj_parser = SuperJobParser()

        # Синонимы для семантической дедупликации
        self.synonyms = {
            "пвз": ["пункт выдачи", "выдача заказов", "пункт выдачи заказов"],
            "менеджер": ["оператор", "сотрудник", "администратор", "специалист"],
            "разработчик": ["developer", "программист", "инженер"],
            "frontend": ["фронтенд", "front-end", "фронт"],
            "backend": ["бэкенд", "back-end", "бэк"],
        }

    async def get_feed(self, filters: FeedFilters) -> FeedResult:
        """
        Получить ленту вакансий с фильтрацией и пагинацией.
        Сначала пробуем из БД, если пусто — fallback на live парсинг.
        """
        print(f"[Feed] Getting feed: query={filters.query}, city={filters.city}, page={filters.page}")

        # Если нет query, возвращаем пустой результат
        if not filters.query:
            return FeedResult(vacancies=[], total=0, page=1, pages=1, has_next=False)

        # 1. Пробуем получить из БД
        vacancies, total = await self._fetch_from_db(filters)

        if vacancies:
            print(f"[Feed] Got {len(vacancies)} from DB (total: {total})")
            pages = (total + filters.limit - 1) // filters.limit if total > 0 else 1
            return FeedResult(
                vacancies=vacancies,
                total=total,
                page=filters.page,
                pages=pages,
                has_next=filters.page < pages,
            )

        # 2. Fallback на live парсинг (если БД пуста)
        print("[Feed] DB empty, falling back to live parsing...")
        vacancies = await self._fetch_all_sources(filters)
        print(f"[Feed] Fetched {len(vacancies)} vacancies from live sources")

        # Дедупликация
        vacancies = self._deduplicate(vacancies)
        print(f"[Feed] After deduplication: {len(vacancies)}")

        # Фильтрация
        vacancies = self._filter(vacancies, filters)
        print(f"[Feed] After filtering: {len(vacancies)}")

        # Сортировка
        vacancies = self._sort(vacancies, filters.sort)

        # Пагинация
        return self._paginate(vacancies, filters.page, filters.limit)

    async def _fetch_from_db(self, filters: FeedFilters) -> tuple[list[Vacancy], int]:
        """Получить вакансии из БД"""
        try:
            offset = (filters.page - 1) * filters.limit

            stored_vacancies, total = await vacancy_storage_service.search_vacancies(
                query=filters.query,
                city=filters.city,
                salary_from=filters.salary_from,
                experience=filters.experience,
                limit=filters.limit,
                offset=offset,
            )

            # Конвертируем в Vacancy
            vacancies = [
                vacancy_storage_service.to_vacancy(sv)
                for sv in stored_vacancies
            ]

            return vacancies, total

        except Exception as e:
            print(f"[Feed] DB fetch error: {e}")
            return [], 0

    async def _fetch_all_sources(self, filters: FeedFilters) -> list[Vacancy]:
        """Получить вакансии со всех источников параллельно (fallback)"""

        search_filters = SearchFilters(
            query=filters.query,
            city=filters.city,
            salary_from=filters.salary_from,
            experience=filters.experience,
        )

        # Запускаем HH и SuperJob параллельно
        tasks = [
            self.hh_parser.search(search_filters, limit=50),
            self.sj_parser.search(search_filters, limit=30),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_vacancies: list[Vacancy] = []

        # Собираем результаты HH и SuperJob
        for i, result in enumerate(results):
            source_name = "HH" if i == 0 else "SuperJob"
            if isinstance(result, list):
                all_vacancies.extend(result)
                print(f"[Feed] {source_name}: {len(result)} vacancies")
            elif isinstance(result, Exception):
                print(f"[Feed] {source_name} error: {result}")

        # Avito последовательно (rate limiting)
        try:
            avito_result = await self.avito_parser.search(search_filters, limit=30)
            all_vacancies.extend(avito_result)
            print(f"[Feed] Avito: {len(avito_result)} vacancies")
        except Exception as e:
            print(f"[Feed] Avito error: {e}")

        return all_vacancies

    def _deduplicate(self, vacancies: list[Vacancy]) -> list[Vacancy]:
        """Удаление дубликатов по ID, title+company+city и семантически"""

        if not vacancies:
            return []

        # 1. Дедупликация по ID
        seen_ids = set()
        unique = []
        for v in vacancies:
            if v.id not in seen_ids:
                seen_ids.add(v.id)
                unique.append(v)

        print(f"[Feed] After ID dedupe: {len(unique)}")

        # 2. Дедупликация по ключу title+company+city
        seen_keys = set()
        unique2 = []
        for v in unique:
            key = f"{v.title.lower()}_{v.company.lower()}_{v.city.lower()}"
            if key not in seen_keys:
                seen_keys.add(key)
                unique2.append(v)

        print(f"[Feed] After key dedupe: {len(unique2)}")

        # 3. Семантическая дедупликация
        return self._semantic_dedupe(unique2)

    def _semantic_dedupe(self, vacancies: list[Vacancy]) -> list[Vacancy]:
        """
        Семантическая дедупликация — группируем похожие вакансии.
        Оставляем лучшую из группы.
        """
        if len(vacancies) <= 1:
            return vacancies

        def normalize(title: str) -> str:
            t = title.lower().strip()
            for word in ["требуется", "нужен", "нужна", "ищем", "вакансия", "работа"]:
                t = t.replace(word, "")
            return t.strip()

        def get_base_key(title: str) -> str:
            t = normalize(title)

            # Заменяем синонимы на базовую форму
            for base, syns in self.synonyms.items():
                for syn in syns:
                    if syn in t:
                        t = t.replace(syn, base)

            # Ключевые слова
            words = [w for w in t.split() if len(w) > 2][:3]
            return " ".join(sorted(words))

        # Группируем по базовому ключу + город
        groups: dict[str, list[Vacancy]] = {}
        for v in vacancies:
            key = f"{get_base_key(v.title)}_{v.city.lower()}"
            if key not in groups:
                groups[key] = []
            groups[key].append(v)

        # Из каждой группы берём лучшую
        result = []
        source_priority = {"hh": 3, "avito": 2, "superjob": 1}

        for key, group in groups.items():
            if len(group) == 1:
                result.append(group[0])
            else:
                group.sort(
                    key=lambda x: (
                        x.salary_from or 0,
                        x.salary_to or 0,
                        len(x.description or ""),
                        source_priority.get(x.source, 0),
                    ),
                    reverse=True
                )
                result.append(group[0])

        return result

    def _filter(self, vacancies: list[Vacancy], filters: FeedFilters) -> list[Vacancy]:
        """Дополнительная фильтрация по параметрам"""
        result = vacancies

        # Фильтр по зарплате
        if filters.salary_from:
            result = [
                v for v in result
                if (v.salary_from and v.salary_from >= filters.salary_from) or
                   (v.salary_to and v.salary_to >= filters.salary_from)
            ]

        # Фильтр по опыту
        if filters.experience:
            result = [v for v in result if v.experience == filters.experience]

        return result

    def _sort(self, vacancies: list[Vacancy], sort_by: str) -> list[Vacancy]:
        """Сортировка вакансий"""

        if sort_by == "salary_desc":
            return sorted(vacancies, key=lambda v: v.salary_from or 0, reverse=True)

        elif sort_by == "salary_asc":
            return sorted(vacancies, key=lambda v: v.salary_from or 999999999)

        elif sort_by == "date":
            return sorted(
                vacancies,
                key=lambda v: v.published_at or datetime.min,
                reverse=True
            )

        # По умолчанию — по релевантности (как есть)
        return vacancies

    def _paginate(self, vacancies: list[Vacancy], page: int, limit: int) -> FeedResult:
        """Пагинация результатов"""
        total = len(vacancies)
        pages = (total + limit - 1) // limit if total > 0 else 1
        offset = (page - 1) * limit
        items = vacancies[offset:offset + limit]

        return FeedResult(
            vacancies=items,
            total=total,
            page=page,
            pages=pages,
            has_next=page < pages
        )


# Singleton
vacancy_feed_service = VacancyFeedService()
