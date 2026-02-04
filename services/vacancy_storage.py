"""
Сервис для хранения вакансий в Supabase
Дедупликация по source + source_id
"""

import httpx
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from config import get_settings
from models.vacancy import Vacancy


class StoredVacancy(BaseModel):
    """Вакансия в БД"""
    id: Optional[str] = None
    source: str
    source_id: str
    title: str
    company: Optional[str] = None
    salary_from: Optional[int] = None
    salary_to: Optional[int] = None
    city: Optional[str] = None
    experience: Optional[str] = None
    employment_type: Optional[str] = None
    description: Optional[str] = None
    url: str
    is_active: bool = True
    last_checked_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class VacancyStorageService:
    """Сервис для работы с vacancies_storage в Supabase"""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.supabase_url
        # Service role key needed for INSERT/UPDATE (RLS only has SELECT policy for anon)
        self.api_key = self.settings.supabase_service_key or self.settings.supabase_key
        self.table = "vacancies_storage"

    def _headers(self) -> dict:
        """Заголовки для Supabase REST API"""
        return {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",  # Не возвращать данные при upsert
        }

    async def save_vacancy(self, vacancy: Vacancy) -> bool:
        """
        Сохранить вакансию. Если существует — обновить.
        UPSERT по source + source_id
        """
        try:
            # Извлекаем source_id из vacancy.id (формат: source_id)
            source_id = vacancy.id

            data = {
                "source": vacancy.source,
                "source_id": source_id,
                "title": vacancy.title,
                "company": vacancy.company,
                "salary_from": vacancy.salary_from,
                "salary_to": vacancy.salary_to,
                "city": vacancy.city,
                "experience": vacancy.experience,
                "employment_type": vacancy.employment_type,
                "description": vacancy.description[:5000] if vacancy.description else None,  # Лимит
                "url": vacancy.url,
                "is_active": True,
                "last_checked_at": datetime.utcnow().isoformat(),
            }

            async with httpx.AsyncClient() as client:
                # UPSERT с on_conflict
                response = await client.post(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"on_conflict": "source,source_id"},  # Указываем колонки для UPSERT
                    headers={
                        **self._headers(),
                        "Prefer": "resolution=merge-duplicates",  # UPSERT
                    },
                    json=data,
                    timeout=10.0,
                )

                if response.status_code in (200, 201, 204):
                    return True
                else:
                    print(f"[Storage] Save error: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            print(f"[Storage] Save exception: {e}")
            return False

    async def save_many(self, vacancies: list[Vacancy]) -> int:
        """Сохранить несколько вакансий. Возвращает количество сохранённых."""
        saved = 0
        for vacancy in vacancies:
            if await self.save_vacancy(vacancy):
                saved += 1
        return saved

    async def get_vacancies(
        self,
        query: Optional[str] = None,
        city: Optional[str] = None,
        salary_from: Optional[int] = None,
        experience: Optional[str] = None,
        is_active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[StoredVacancy]:
        """Получить вакансии из БД с фильтрами"""
        try:
            params = {
                "select": "*",
                "is_active": f"eq.{str(is_active).lower()}",
                "order": "created_at.desc",
                "limit": str(limit),
                "offset": str(offset),
            }

            if city:
                params["city"] = f"ilike.%{city}%"

            if salary_from:
                params["or"] = f"(salary_from.gte.{salary_from},salary_to.gte.{salary_from})"

            if experience:
                params["experience"] = f"eq.{experience}"

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params=params,
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    return [StoredVacancy(**row) for row in data]
                else:
                    print(f"[Storage] Get error: {response.status_code}")
                    return []

        except Exception as e:
            print(f"[Storage] Get exception: {e}")
            return []

    async def get_all_vacancies(
        self,
        city: Optional[str] = None,
        salary_from: Optional[int] = None,
        experience: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[StoredVacancy], int]:
        """
        Получить все активные вакансии с пагинацией.
        Возвращает (вакансии, общее количество)
        Поддерживает несколько городов через запятую.
        """
        try:
            # Парсим города
            cities = []
            if city:
                cities = [c.strip().lower() for c in city.split(",") if c.strip()]

            # Маппинг experience для поиска по разным форматам
            exp_patterns = self._get_experience_patterns(experience)

            # Если есть фильтр по опыту, получаем больше данных для фильтрации в Python
            fetch_limit = limit * 10 if exp_patterns else limit
            fetch_offset = 0 if exp_patterns else offset

            params = {
                "select": "*",
                "is_active": "eq.true",
                "order": "created_at.desc",
                "limit": str(fetch_limit),
                "offset": str(fetch_offset),
            }

            # Поддержка нескольких городов через запятую
            if cities:
                if len(cities) == 1:
                    params["city"] = f"ilike.%{cities[0]}%"
                else:
                    # OR запрос для нескольких городов
                    city_conditions = ",".join([f"city.ilike.%{c}%" for c in cities])
                    params["or"] = f"({city_conditions})"

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params=params,
                    headers={
                        **self._headers(),
                        "Prefer": "count=exact",
                    },
                    timeout=15.0,
                )

                # 200 = все данные, 206 = частичные данные (пагинация)
                if response.status_code in (200, 206):
                    data = response.json()
                    vacancies = [StoredVacancy(**row) for row in data]

                    # Фильтруем по опыту в Python
                    if exp_patterns:
                        vacancies = [
                            v for v in vacancies
                            if self._matches_experience(v.experience, exp_patterns)
                        ]
                        total = len(vacancies)
                        # Применяем пагинацию
                        vacancies = vacancies[offset:offset + limit]
                    else:
                        # Получаем count из заголовка
                        content_range = response.headers.get("content-range", "")
                        total = 0
                        if "/" in content_range:
                            total = int(content_range.split("/")[1])

                    print(f"[Storage] Got {len(vacancies)} vacancies, total: {total}")
                    return vacancies, total
                else:
                    print(f"[Storage] Get all error: {response.status_code}")
                    return [], 0

        except Exception as e:
            print(f"[Storage] Get all exception: {e}")
            return [], 0

    async def search_vacancies(
        self,
        query: str,
        city: Optional[str] = None,
        salary_from: Optional[int] = None,
        experience: Optional[str] = None,
        employment_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[StoredVacancy], int]:
        """
        Полнотекстовый поиск по вакансиям.
        Возвращает (вакансии, общее количество)
        Поддерживает несколько городов через запятую.
        """
        try:
            # Парсим города
            cities = []
            if city:
                cities = [c.strip().lower() for c in city.split(",") if c.strip()]

            # Маппинг experience для поиска по разным форматам
            exp_patterns = self._get_experience_patterns(experience)

            async with httpx.AsyncClient() as client:
                # Получаем вакансии
                params = {
                    "select": "*",
                    "is_active": "eq.true",
                    "or": f"(title.ilike.%{query}%,description.ilike.%{query}%,company.ilike.%{query}%)",
                    "order": "created_at.desc",
                }

                # Для одного города добавляем фильтр в запрос
                if len(cities) == 1:
                    params["city"] = f"ilike.%{cities[0]}%"
                    params["limit"] = str(limit)
                    params["offset"] = str(offset)
                else:
                    # Для нескольких городов получаем больше и фильтруем в Python
                    params["limit"] = str(limit * 5) if cities else str(limit)
                    params["offset"] = "0" if cities else str(offset)

                response = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params=params,
                    headers={
                        **self._headers(),
                        "Prefer": "count=exact",
                    },
                    timeout=15.0,
                )

                # 200 = все данные, 206 = частичные данные (пагинация)
                if response.status_code in (200, 206):
                    data = response.json()
                    vacancies = [StoredVacancy(**row) for row in data]

                    # Фильтруем по нескольким городам в Python
                    if len(cities) > 1:
                        vacancies = [
                            v for v in vacancies
                            if v.city and any(c in v.city.lower() for c in cities)
                        ]

                    # Фильтруем по опыту в Python
                    if exp_patterns:
                        vacancies = [
                            v for v in vacancies
                            if self._matches_experience(v.experience, exp_patterns)
                        ]

                    # Фильтруем по типу занятости (удаленка и т.д.)
                    if employment_type:
                        vacancies = [
                            v for v in vacancies
                            if v.employment_type == employment_type
                        ]

                    total = len(vacancies)
                    # Применяем пагинацию
                    vacancies = vacancies[offset:offset + limit]

                    return vacancies, total
                else:
                    print(f"[Storage] Search error: {response.status_code}")
                    return [], 0

        except Exception as e:
            print(f"[Storage] Search exception: {e}")
            return [], 0

    def _get_experience_patterns(self, experience: Optional[str]) -> list[str]:
        """
        Маппинг experience в паттерны для поиска.
        Разные источники могут хранить опыт в разных форматах.
        """
        if not experience:
            return []

        patterns_map = {
            "no_experience": [
                "no_experience", "noexperience", "без опыта", "не требуется",
                "нет опыта", "without experience", "no experience"
            ],
            "1-3": [
                "1-3", "between1and3", "от 1 до 3", "1 до 3", "1-3 года",
                "от 1 года до 3 лет", "1–3"
            ],
            "3-6": [
                "3-6", "between3and6", "от 3 до 6", "3 до 6", "3-6 лет",
                "от 3 лет до 6 лет", "3–6"
            ],
            "6+": [
                "6+", "morethan6", "более 6", "больше 6", "от 6 лет",
                "more than 6", "6 лет и более"
            ],
        }

        return patterns_map.get(experience, [experience])

    def _matches_experience(self, vacancy_exp: Optional[str], patterns: list[str]) -> bool:
        """Проверить, совпадает ли опыт вакансии с паттернами"""
        if not vacancy_exp:
            return False

        vacancy_exp_lower = vacancy_exp.lower()
        return any(p.lower() in vacancy_exp_lower for p in patterns)

    async def get_for_verification(self, limit: int = 100) -> list[StoredVacancy]:
        """
        Получить вакансии для проверки (oldest first by last_checked_at).
        Возвращает активные вакансии, которые давно не проверялись.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={
                        "select": "*",
                        "is_active": "eq.true",
                        "order": "last_checked_at.asc.nullsfirst",
                        "limit": str(limit),
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    return [StoredVacancy(**row) for row in response.json()]
                else:
                    print(f"[Storage] Verification get error: {response.status_code}")
                    return []

        except Exception as e:
            print(f"[Storage] Verification get exception: {e}")
            return []

    async def get_vacancies_for_moderation(self, limit: int = 500) -> list[Vacancy]:
        """
        Умная выборка вакансий для модерации с приоритизацией:
        1. Новые вакансии (созданные за последние 24 часа) - ПРИОРИТЕТ
        2. Старые непроверенные (moderation_checked_at IS NULL)

        Возвращает до limit активных вакансий.
        """
        try:
            from datetime import timedelta

            all_vacancies = []

            # ПРИОРИТЕТ 1: Новые вакансии за последние 24 часа
            cutoff_time = datetime.utcnow() - timedelta(hours=24)
            cutoff_iso = cutoff_time.isoformat()

            async with httpx.AsyncClient() as client:
                # Получаем новые вакансии
                response = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={
                        "select": "*",
                        "is_active": "eq.true",
                        "created_at": f"gte.{cutoff_iso}",
                        "order": "created_at.desc",
                        "limit": str(limit),
                    },
                    headers=self._headers(),
                    timeout=15.0,
                )

                if response.status_code == 200:
                    new_vacancies = [StoredVacancy(**row) for row in response.json()]
                    all_vacancies.extend(new_vacancies)
                    print(f"[Storage] Found {len(new_vacancies)} new vacancies (last 24h)")

                # Если новых мало - добираем старые непроверенные
                remaining = limit - len(all_vacancies)
                if remaining > 0:
                    response = await client.get(
                        f"{self.base_url}/rest/v1/{self.table}",
                        params={
                            "select": "*",
                            "is_active": "eq.true",
                            "moderation_checked_at": "is.null",
                            "order": "created_at.asc",  # Старые первыми
                            "limit": str(remaining),
                        },
                        headers=self._headers(),
                        timeout=15.0,
                    )

                    if response.status_code == 200:
                        old_vacancies = [StoredVacancy(**row) for row in response.json()]
                        all_vacancies.extend(old_vacancies)
                        print(f"[Storage] Found {len(old_vacancies)} old unchecked vacancies")

                # Конвертируем в Vacancy
                result = [self.to_vacancy(sv) for sv in all_vacancies]
                print(f"[Storage] Total for moderation: {len(result)} vacancies")
                return result

        except Exception as e:
            print(f"[Storage] Moderation get exception: {e}")
            return []

    async def mark_inactive(self, vacancy_id: str) -> bool:
        """Пометить вакансию как неактивную"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"id": f"eq.{vacancy_id}"},
                    headers=self._headers(),
                    json={
                        "is_active": False,
                        "last_checked_at": datetime.utcnow().isoformat(),
                    },
                    timeout=10.0,
                )

                return response.status_code in (200, 204)

        except Exception as e:
            print(f"[Storage] Mark inactive exception: {e}")
            return False

    async def deactivate_vacancy(self, vacancy_id: str) -> bool:
        """Деактивировать вакансию + отметить время модерации"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"id": f"eq.{vacancy_id}"},
                    headers=self._headers(),
                    json={
                        "is_active": False,
                        "last_checked_at": datetime.utcnow().isoformat(),
                        "moderation_checked_at": datetime.utcnow().isoformat(),
                    },
                    timeout=10.0,
                )

                return response.status_code in (200, 204)

        except Exception as e:
            print(f"[Storage] Deactivate exception: {e}")
            return False

    async def mark_moderation_checked(self, vacancy_id: str) -> bool:
        """Отметить вакансию как проверенную модерацией"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"id": f"eq.{vacancy_id}"},
                    headers=self._headers(),
                    json={"moderation_checked_at": datetime.utcnow().isoformat()},
                    timeout=10.0,
                )

                return response.status_code in (200, 204)

        except Exception as e:
            print(f"[Storage] Mark moderation exception: {e}")
            return False

    async def update_last_checked(self, vacancy_id: str) -> bool:
        """Обновить время последней проверки"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"id": f"eq.{vacancy_id}"},
                    headers=self._headers(),
                    json={"last_checked_at": datetime.utcnow().isoformat()},
                    timeout=10.0,
                )

                return response.status_code in (200, 204)

        except Exception as e:
            print(f"[Storage] Update checked exception: {e}")
            return False

    async def get_stats(self) -> dict:
        """Получить статистику по вакансиям"""
        try:
            async with httpx.AsyncClient() as client:
                # Активные
                active_resp = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"select": "id", "is_active": "eq.true"},
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )
                active_count = 0
                if active_resp.status_code in (200, 206) and "content-range" in active_resp.headers:
                    active_count = int(active_resp.headers["content-range"].split("/")[1])

                # Неактивные
                inactive_resp = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"select": "id", "is_active": "eq.false"},
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )
                inactive_count = 0
                if inactive_resp.status_code in (200, 206) and "content-range" in inactive_resp.headers:
                    inactive_count = int(inactive_resp.headers["content-range"].split("/")[1])

                return {
                    "active": active_count,
                    "inactive": inactive_count,
                    "total": active_count + inactive_count,
                }

        except Exception as e:
            print(f"[Storage] Stats exception: {e}")
            return {"active": 0, "inactive": 0, "total": 0}

    async def get_moderation_stats(self) -> dict:
        """Получить статистику модерации"""
        try:
            async with httpx.AsyncClient() as client:
                # Непроверенные (активные без moderation_checked_at)
                unchecked_resp = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={
                        "select": "id",
                        "is_active": "eq.true",
                        "moderation_checked_at": "is.null",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )
                unchecked_count = 0
                if unchecked_resp.status_code in (200, 206) and "content-range" in unchecked_resp.headers:
                    unchecked_count = int(unchecked_resp.headers["content-range"].split("/")[1])

                # Проверенные
                checked_resp = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={
                        "select": "id",
                        "is_active": "eq.true",
                        "moderation_checked_at": "not.is.null",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )
                checked_count = 0
                if checked_resp.status_code in (200, 206) and "content-range" in checked_resp.headers:
                    checked_count = int(checked_resp.headers["content-range"].split("/")[1])

                return {
                    "unchecked": unchecked_count,
                    "checked": checked_count,
                    "total_active": unchecked_count + checked_count,
                }

        except Exception as e:
            print(f"[Storage] Moderation stats exception: {e}")
            return {"unchecked": 0, "checked": 0, "total_active": 0}

    def to_vacancy(self, stored: StoredVacancy) -> Vacancy:
        """Конвертировать StoredVacancy в Vacancy"""
        return Vacancy(
            id=f"{stored.source}_{stored.source_id}",
            title=stored.title,
            company=stored.company or "",
            salary_from=stored.salary_from,
            salary_to=stored.salary_to,
            city=stored.city or "",
            experience=stored.experience,
            employment_type=stored.employment_type,
            description=stored.description or "",
            url=stored.url,
            source=stored.source,
        )


# Singleton
vacancy_storage_service = VacancyStorageService()
