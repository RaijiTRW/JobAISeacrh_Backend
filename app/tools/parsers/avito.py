"""
Парсер вакансий с Avito
Использует веб-скрапинг (Avito не имеет публичного API)
"""

import httpx
from typing import Optional
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import quote
from app.tools.parsers import BaseParser
from app.models.vacancy import Vacancy, SearchFilters


class AvitoParser(BaseParser):
    name = "avito"
    base_url = "https://www.avito.ru"

    # Маппинг городов на slug
    CITY_SLUGS = {
        "москва": "moskva",
        "санкт-петербург": "sankt-peterburg",
        "спб": "sankt-peterburg",
        "питер": "sankt-peterburg",
        "новосибирск": "novosibirsk",
        "екатеринбург": "ekaterinburg",
        "казань": "kazan",
        "нижний новгород": "nizhniy_novgorod",
        "краснодар": "krasnodar",
        "ростов-на-дону": "rostov-na-donu",
        "воронеж": "voronezh",
        "пермь": "perm",
    }

    async def search(self, filters: SearchFilters, limit: int = 20) -> list[Vacancy]:
        """Поиск вакансий на Avito"""
        vacancies = []

        try:
            city_slug = self._get_city_slug(filters.city) if filters.city else "rossiya"
            url = f"{self.base_url}/{city_slug}/vakansii"

            params = {
                "q": filters.query,
            }

            # Фильтр зарплаты
            if filters.salary_from:
                params["pmin"] = filters.salary_from

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-RU,ru;q=0.9",
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=15.0,
                    follow_redirects=True,
                )

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "lxml")
                    items = soup.select("[data-marker='item']")[:limit]

                    for item in items:
                        vacancy = self._parse_vacancy(item, filters.city or city_slug)
                        if vacancy and self.matches_filters(vacancy, filters):
                            vacancies.append(vacancy)

        except Exception as e:
            print(f"Avito Parser error: {e}")

        return vacancies

    def _get_city_slug(self, city: str) -> str:
        """Получить slug города для URL"""
        city_lower = city.lower()
        return self.CITY_SLUGS.get(city_lower, city_lower.replace(" ", "_"))

    def _parse_vacancy(self, item, city: str) -> Optional[Vacancy]:
        """Парсинг вакансии из HTML"""
        try:
            # ID
            item_id = item.get("data-item-id", "")

            # Название
            title_elem = item.select_one("[itemprop='name']")
            title = title_elem.get_text(strip=True) if title_elem else ""

            # Ссылка
            link_elem = item.select_one("a[itemprop='url']")
            url = self.base_url + link_elem["href"] if link_elem else ""

            # Цена/Зарплата
            price_elem = item.select_one("[itemprop='price']")
            salary_from = None
            salary_to = None
            if price_elem:
                price_text = price_elem.get("content", "")
                if price_text:
                    try:
                        salary_from = int(price_text)
                    except ValueError:
                        pass

            # Описание
            desc_elem = item.select_one("[class*='description']")
            description = desc_elem.get_text(strip=True) if desc_elem else ""

            if not title or not item_id:
                return None

            return Vacancy(
                id=f"avito_{item_id}",
                title=title,
                company="Работодатель на Avito",
                salary_from=salary_from,
                salary_to=salary_to,
                city=city.title(),
                description=description[:500],
                url=url,
                source="avito",
            )

        except Exception as e:
            print(f"Error parsing Avito vacancy: {e}")
            return None
