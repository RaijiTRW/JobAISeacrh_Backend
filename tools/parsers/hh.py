"""
Парсер вакансий с hh.ru
Использует публичное API hh.ru
"""

import httpx
from typing import Optional
from datetime import datetime
from tools.parsers import BaseParser
from models.vacancy import Vacancy, SearchFilters


class HHParser(BaseParser):
    name = "hh"
    base_url = "https://api.hh.ru"

    # Маппинг опыта
    EXPERIENCE_MAP = {
        "no_experience": "noExperience",
        "1-3": "between1And3",
        "3-6": "between3And6",
        "6+": "moreThan6",
    }

    # Маппинг типа занятости
    EMPLOYMENT_MAP = {
        "full": "full",
        "part": "part",
        "remote": "remote",
    }

    async def search(self, filters: SearchFilters, limit: int = 20) -> list[Vacancy]:
        """Поиск вакансий через API hh.ru"""
        vacancies = []

        try:
            params = {
                "text": filters.query,
                "per_page": min(limit, 100),
                "page": 0,
            }

            # Город
            if filters.city:
                area_id = await self._get_area_id(filters.city)
                if area_id:
                    params["area"] = area_id
                else:
                    print(f"[HH] Warning: city '{filters.city}' not found")

            # Зарплата
            if filters.salary_from:
                params["salary"] = filters.salary_from
                params["only_with_salary"] = "true"

            # Опыт
            if filters.experience and filters.experience in self.EXPERIENCE_MAP:
                params["experience"] = self.EXPERIENCE_MAP[filters.experience]

            # Тип занятости
            if filters.employment_type:
                if filters.employment_type == "remote":
                    params["schedule"] = "remote"
                elif filters.employment_type in self.EMPLOYMENT_MAP:
                    params["employment"] = self.EMPLOYMENT_MAP[filters.employment_type]

            print(f"[HH] Searching with params: {params}")

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/vacancies",
                    params=params,
                    headers={"User-Agent": "JobSearchApp/1.0"},
                    timeout=15.0,
                )
                print(f"[HH] Response status: {response.status_code}")
                response.raise_for_status()
                data = response.json()

                total_found = data.get("found", 0)
                items = data.get("items", [])
                print(f"[HH] Found {total_found} total, got {len(items)} items")

                for item in items:
                    vacancy = self._parse_vacancy(item)
                    if vacancy and self.matches_filters(vacancy, filters):
                        vacancies.append(vacancy)

        except Exception as e:
            print(f"[HH] Parser error: {e}")
            import traceback
            traceback.print_exc()

        print(f"[HH] Total vacancies: {len(vacancies)}")
        return vacancies

    async def _get_area_id(self, city: str) -> Optional[str]:
        """Получить ID региона по названию города"""
        city_lower = city.lower()

        # Кэш популярных городов
        CITY_CACHE = {
            "москва": "1",
            "санкт-петербург": "2",
            "спб": "2",
            "питер": "2",
            "новосибирск": "4",
            "екатеринбург": "3",
            "казань": "88",
            "нижний новгород": "66",
            "краснодар": "53",
            "ростов-на-дону": "76",
            "воронеж": "26",
            "пермь": "72",
            "новороссийск": "1061",
            "сочи": "237",
            "волгоград": "24",
            "самара": "78",
            "уфа": "99",
            "красноярск": "54",
            "омск": "68",
            "челябинск": "104",
        }

        if city_lower in CITY_CACHE:
            return CITY_CACHE[city_lower]

        # Поиск через API
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/suggests/areas",
                    params={"text": city},
                    timeout=10.0,
                )
                data = response.json()
                if data.get("items"):
                    return data["items"][0]["id"]
        except Exception:
            pass

        return None

    def _parse_vacancy(self, item: dict) -> Optional[Vacancy]:
        """Парсинг вакансии из ответа API"""
        try:
            salary = item.get("salary") or {}

            return Vacancy(
                id=f"hh_{item['id']}",
                title=item["name"],
                company=item.get("employer", {}).get("name", "Компания не указана"),
                salary_from=salary.get("from"),
                salary_to=salary.get("to"),
                salary_currency=salary.get("currency", "RUR"),
                city=item.get("area", {}).get("name", ""),
                experience=item.get("experience", {}).get("name"),
                employment_type=self._map_employment(item),
                description=item.get("snippet", {}).get("responsibility", "") or "",
                url=item.get("alternate_url", ""),
                source="hh",
                published_at=datetime.fromisoformat(
                    item["published_at"].replace("Z", "+00:00")
                ) if item.get("published_at") else None,
            )
        except Exception as e:
            print(f"Error parsing HH vacancy: {e}")
            return None

    def _map_employment(self, item: dict) -> Optional[str]:
        """Определение типа занятости"""
        schedule = item.get("schedule", {}).get("id", "")
        if schedule == "remote":
            return "remote"

        employment = item.get("employment", {}).get("id", "")
        if employment == "full":
            return "full"
        elif employment == "part":
            return "part"

        return None
