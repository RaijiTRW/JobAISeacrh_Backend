"""
Сервис для работы с вакансиями работодателей
"""
import httpx
from datetime import datetime
from typing import Optional

from config import get_settings
from models.employer_vacancy import (
    EmployerVacancy,
    EmployerVacancyCreate,
    EmployerVacancyUpdate,
    EmployerVacancyList,
    VacancyStats,
)
from models.vacancy import Vacancy


class EmployerVacancyService:
    """Сервис для CRUD операций с вакансиями работодателей"""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.supabase_url
        self.api_key = self.settings.supabase_key
        self.table = "employer_vacancies"

    def _headers(self, user_token: Optional[str] = None) -> dict:
        """Заголовки для Supabase REST API"""
        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }
        if user_token:
            headers["Authorization"] = f"Bearer {user_token}"
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def create(
        self,
        vacancy: EmployerVacancyCreate,
        user_id: str,
        user_token: str,
    ) -> Optional[EmployerVacancy]:
        """Создать вакансию"""
        try:
            data = {
                "user_id": user_id,
                "title": vacancy.title,
                "company": vacancy.company,
                "city": vacancy.city,
                "salary_from": vacancy.salary_from,
                "salary_to": vacancy.salary_to,
                "salary_currency": vacancy.salary_currency,
                "experience": vacancy.experience,
                "employment_type": vacancy.employment_type,
                "schedule": vacancy.schedule,
                "description": vacancy.description,
                "requirements": vacancy.requirements,
                "conditions": vacancy.conditions,
                "contact_name": vacancy.contact_name,
                "contact_email": vacancy.contact_email,
                "contact_phone": vacancy.contact_phone,
                "status": "draft",
                "is_active": True,
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/rest/v1/{self.table}",
                    headers={
                        **self._headers(user_token),
                        "Prefer": "return=representation",
                    },
                    json=data,
                    timeout=15.0,
                )

                if response.status_code in (200, 201):
                    result = response.json()
                    if result and len(result) > 0:
                        return EmployerVacancy(**result[0])
                else:
                    print(f"[EmployerVacancy] Create error: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            print(f"[EmployerVacancy] Create exception: {e}")
            return None

    async def get_by_id(
        self,
        vacancy_id: str,
        user_token: Optional[str] = None,
    ) -> Optional[EmployerVacancy]:
        """Получить вакансию по ID"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"id": f"eq.{vacancy_id}", "select": "*"},
                    headers=self._headers(user_token),
                    timeout=10.0,
                )

                if response.status_code in (200, 206):
                    result = response.json()
                    if result and len(result) > 0:
                        return EmployerVacancy(**result[0])
                return None

        except Exception as e:
            print(f"[EmployerVacancy] Get by ID exception: {e}")
            return None

    async def get_user_vacancies(
        self,
        user_id: str,
        user_token: str,
        page: int = 1,
        limit: int = 20,
    ) -> EmployerVacancyList:
        """Получить вакансии пользователя"""
        try:
            offset = (page - 1) * limit

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={
                        "user_id": f"eq.{user_id}",
                        "select": "*",
                        "order": "created_at.desc",
                        "limit": str(limit),
                        "offset": str(offset),
                    },
                    headers={
                        **self._headers(user_token),
                        "Prefer": "count=exact",
                    },
                    timeout=15.0,
                )

                if response.status_code in (200, 206):
                    data = response.json()
                    content_range = response.headers.get("content-range", "")
                    total = 0
                    if "/" in content_range:
                        total = int(content_range.split("/")[1])

                    vacancies = [EmployerVacancy(**row) for row in data]
                    pages = (total + limit - 1) // limit if total > 0 else 1

                    return EmployerVacancyList(
                        vacancies=vacancies,
                        total=total,
                        page=page,
                        pages=pages,
                        has_next=page < pages,
                    )

                return EmployerVacancyList(vacancies=[], total=0, page=1, pages=1, has_next=False)

        except Exception as e:
            print(f"[EmployerVacancy] Get user vacancies exception: {e}")
            return EmployerVacancyList(vacancies=[], total=0, page=1, pages=1, has_next=False)

    async def update(
        self,
        vacancy_id: str,
        updates: EmployerVacancyUpdate,
        user_token: str,
    ) -> Optional[EmployerVacancy]:
        """Обновить вакансию"""
        try:
            # Фильтруем None значения
            data = {k: v for k, v in updates.model_dump().items() if v is not None}
            if not data:
                return await self.get_by_id(vacancy_id, user_token)

            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"id": f"eq.{vacancy_id}"},
                    headers={
                        **self._headers(user_token),
                        "Prefer": "return=representation",
                    },
                    json=data,
                    timeout=15.0,
                )

                if response.status_code in (200, 204):
                    result = response.json()
                    if result and len(result) > 0:
                        return EmployerVacancy(**result[0])
                else:
                    print(f"[EmployerVacancy] Update error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"[EmployerVacancy] Update exception: {e}")
            return None

    async def delete(self, vacancy_id: str, user_token: str) -> bool:
        """Удалить вакансию"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.delete(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"id": f"eq.{vacancy_id}"},
                    headers=self._headers(user_token),
                    timeout=10.0,
                )

                return response.status_code in (200, 204)

        except Exception as e:
            print(f"[EmployerVacancy] Delete exception: {e}")
            return False

    async def publish(self, vacancy_id: str, user_token: str) -> Optional[EmployerVacancy]:
        """Опубликовать вакансию"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"id": f"eq.{vacancy_id}"},
                    headers={
                        **self._headers(user_token),
                        "Prefer": "return=representation",
                    },
                    json={
                        "status": "published",
                        "published_at": datetime.utcnow().isoformat(),
                    },
                    timeout=10.0,
                )

                if response.status_code in (200, 204):
                    result = response.json()
                    if result and len(result) > 0:
                        return EmployerVacancy(**result[0])
                return None

        except Exception as e:
            print(f"[EmployerVacancy] Publish exception: {e}")
            return None

    async def close(self, vacancy_id: str, user_token: str) -> Optional[EmployerVacancy]:
        """Закрыть вакансию"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={"id": f"eq.{vacancy_id}"},
                    headers={
                        **self._headers(user_token),
                        "Prefer": "return=representation",
                    },
                    json={
                        "status": "closed",
                        "is_active": False,
                    },
                    timeout=10.0,
                )

                if response.status_code in (200, 204):
                    result = response.json()
                    if result and len(result) > 0:
                        return EmployerVacancy(**result[0])
                return None

        except Exception as e:
            print(f"[EmployerVacancy] Close exception: {e}")
            return None

    async def get_published_count(self) -> int:
        """Получить количество опубликованных вакансий (наших)"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/{self.table}",
                    params={
                        "select": "id",
                        "status": "eq.published",
                        "is_active": "eq.true",
                    },
                    headers={
                        **self._headers(),
                        "Prefer": "count=exact",
                    },
                    timeout=10.0,
                )

                if response.status_code in (200, 206):
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        return int(content_range.split("/")[1])
                return 0

        except Exception as e:
            print(f"[EmployerVacancy] Get published count exception: {e}")
            return 0

    async def get_published_vacancies(
        self,
        query: Optional[str] = None,
        city: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[EmployerVacancy], int]:
        """Получить опубликованные вакансии для ленты"""
        try:
            params = {
                "select": "*",
                "status": "eq.published",
                "is_active": "eq.true",
                "order": "published_at.desc",
                "limit": str(limit),
                "offset": str(offset),
            }

            if city:
                params["city"] = f"ilike.%{city}%"

            # Поиск по тексту
            if query:
                params["or"] = f"(title.ilike.%{query}%,description.ilike.%{query}%,company.ilike.%{query}%)"

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

                if response.status_code in (200, 206):
                    data = response.json()
                    content_range = response.headers.get("content-range", "")
                    total = 0
                    if "/" in content_range:
                        total = int(content_range.split("/")[1])

                    vacancies = [EmployerVacancy(**row) for row in data]
                    return vacancies, total

                return [], 0

        except Exception as e:
            print(f"[EmployerVacancy] Get published exception: {e}")
            return [], 0

    def to_vacancy(self, ev: EmployerVacancy) -> Vacancy:
        """Конвертировать EmployerVacancy в Vacancy для ленты"""
        return Vacancy(
            id=f"platform_{ev.id}",
            title=ev.title,
            company=ev.company,
            salary_from=ev.salary_from,
            salary_to=ev.salary_to,
            city=ev.city,
            experience=ev.experience,
            employment_type=ev.employment_type,
            description=ev.description,
            url=f"/vacancies/{ev.id}",  # Внутренняя ссылка
            source="platform",
            published_at=ev.published_at,
        )


# Singleton
employer_vacancy_service = EmployerVacancyService()
