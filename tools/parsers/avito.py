"""
Парсер вакансий с Avito
Использует веб-скрапинг (Avito не имеет публичного API)
ВАЖНО: Avito активно защищается от скрапинга, парсер может работать нестабильно
"""

import httpx
import re
from typing import Optional
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import quote
from tools.parsers import BaseParser
from models.vacancy import Vacancy, SearchFilters


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
        "самара": "samara",
        "уфа": "ufa",
        "челябинск": "chelyabinsk",
        "омск": "omsk",
        "красноярск": "krasnoyarsk",
        "волгоград": "volgograd",
        "тюмень": "tyumen",
    }

    async def search(self, filters: SearchFilters, limit: int = 20) -> list[Vacancy]:
        """Поиск вакансий на Avito"""
        vacancies = []

        try:
            city_slug = self._get_city_slug(filters.city) if filters.city else "rossiya"
            url = f"{self.base_url}/{city_slug}/vakansii"

            params = {}
            if filters.query:
                params["q"] = filters.query

            # Фильтр зарплаты
            if filters.salary_from:
                params["pmin"] = filters.salary_from

            # Более реалистичные заголовки браузера
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0",
            }

            print(f"[Avito] Searching: {url} with params: {params}")

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=20.0,
                    follow_redirects=True,
                )

                print(f"[Avito] Response status: {response.status_code}")

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")

                    # Avito использует разные селекторы, пробуем несколько
                    items = []

                    # Основной селектор
                    items = soup.select("[data-marker='item']")
                    print(f"[Avito] Found {len(items)} items with [data-marker='item']")

                    # Альтернативные селекторы
                    if not items:
                        items = soup.select("[class*='iva-item']")
                        print(f"[Avito] Found {len(items)} items with [class*='iva-item']")

                    if not items:
                        items = soup.select("[itemtype*='JobPosting']")
                        print(f"[Avito] Found {len(items)} items with itemtype JobPosting")

                    if not items:
                        # Ищем по структуре списка
                        items = soup.select("div[class*='items-'] > div")
                        print(f"[Avito] Found {len(items)} items in items container")

                    items = items[:limit]

                    for item in items:
                        vacancy = self._parse_vacancy(item, filters.city or city_slug)
                        if vacancy and self.matches_filters(vacancy, filters):
                            vacancies.append(vacancy)

                elif response.status_code == 403:
                    print("[Avito] Access denied (403) - Avito blocked the request")
                elif response.status_code == 429:
                    print("[Avito] Rate limited (429) - too many requests")
                else:
                    print(f"[Avito] Unexpected status: {response.status_code}")

        except httpx.TimeoutException:
            print("[Avito] Request timeout")
        except Exception as e:
            print(f"[Avito] Parser error: {e}")
            import traceback
            traceback.print_exc()

        print(f"[Avito] Total vacancies found: {len(vacancies)}")
        return vacancies

    def _get_city_slug(self, city: str) -> str:
        """Получить slug города для URL"""
        city_lower = city.lower().strip()
        return self.CITY_SLUGS.get(city_lower, city_lower.replace(" ", "_").replace("-", "_"))

    def _parse_vacancy(self, item, city: str) -> Optional[Vacancy]:
        """Парсинг вакансии из HTML"""
        try:
            # ID
            item_id = item.get("data-item-id", "") or item.get("id", "")
            if not item_id:
                # Пробуем найти ID в ссылке
                link = item.select_one("a[href*='/vakansii/']")
                if link:
                    href = link.get("href", "")
                    match = re.search(r"_(\d+)$", href)
                    if match:
                        item_id = match.group(1)

            # Название - пробуем разные селекторы
            title = ""
            title_selectors = [
                "[itemprop='name']",
                "h3[data-marker='item-title']",
                "[class*='title'] a",
                "a[title]",
                "a[href*='/vakansii/']"
            ]
            for selector in title_selectors:
                title_elem = item.select_one(selector)
                if title_elem:
                    title = title_elem.get_text(strip=True) or title_elem.get("title", "")
                    if title:
                        break

            # Ссылка
            url = ""
            link_elem = item.select_one("a[itemprop='url']") or item.select_one("a[href*='/vakansii/']")
            if link_elem:
                href = link_elem.get("href", "")
                url = href if href.startswith("http") else self.base_url + href

            # Цена/Зарплата
            salary_from = None
            salary_to = None

            price_selectors = [
                "[itemprop='price']",
                "[data-marker='item-price']",
                "[class*='price']",
            ]
            for selector in price_selectors:
                price_elem = item.select_one(selector)
                if price_elem:
                    price_text = price_elem.get("content", "") or price_elem.get_text(strip=True)
                    salary_from, salary_to = self._parse_salary(price_text)
                    if salary_from or salary_to:
                        break

            # Компания
            company = "Работодатель на Avito"
            company_elem = item.select_one("[data-marker='item-address']")
            if company_elem:
                company_text = company_elem.get_text(strip=True)
                if company_text:
                    company = company_text

            # Описание
            description = ""
            desc_selectors = [
                "[class*='description']",
                "[data-marker='item-description']",
                "[class*='snippet']"
            ]
            for selector in desc_selectors:
                desc_elem = item.select_one(selector)
                if desc_elem:
                    description = desc_elem.get_text(strip=True)[:500]
                    if description:
                        break

            if not title:
                return None

            return Vacancy(
                id=f"avito_{item_id or hash(title)}",
                title=title,
                company=company,
                salary_from=salary_from,
                salary_to=salary_to,
                city=city.title() if city else "Россия",
                description=description,
                url=url,
                source="avito",
            )

        except Exception as e:
            print(f"[Avito] Error parsing vacancy: {e}")
            return None

    def _parse_salary(self, text: str) -> tuple[Optional[int], Optional[int]]:
        """Парсинг зарплаты из текста"""
        if not text:
            return None, None

        # Убираем пробелы и символы
        text = text.replace("\xa0", "").replace(" ", "").replace("₽", "").lower()

        salary_from = None
        salary_to = None

        try:
            # Находим все числа
            numbers = re.findall(r"\d+", text)

            if "от" in text and "до" in text and len(numbers) >= 2:
                salary_from = int(numbers[0])
                salary_to = int(numbers[1])
            elif "от" in text and numbers:
                salary_from = int(numbers[0])
            elif "до" in text and numbers:
                salary_to = int(numbers[0])
            elif numbers:
                salary_from = int(numbers[0])
        except ValueError:
            pass

        return salary_from, salary_to
