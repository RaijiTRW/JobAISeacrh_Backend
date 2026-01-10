"""
Парсер вакансий с Avito
Использует веб-скрапинг (Avito не имеет публичного API)
ВАЖНО: Avito активно защищается от скрапинга, парсер может работать нестабильно
"""

import httpx
import asyncio
import random
import re
from typing import Optional
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import quote
from tools.parsers import BaseParser
from models.vacancy import Vacancy, SearchFilters
from config import get_settings


class AvitoParser(BaseParser):
    name = "avito"
    base_url = "https://www.avito.ru"
    _last_request_time = 0  # Для rate limiting
    _request_count = 0  # Счётчик запросов для ротации
    _proxy_list: list[str] = []  # Список прокси для ротации
    _proxy_index = 0  # Текущий индекс прокси

    @classmethod
    def _get_proxy(cls) -> Optional[str]:
        """Получить следующий прокси из списка (ротация)"""
        settings = get_settings()
        if not settings.proxy_urls:
            return None

        # Инициализируем список прокси при первом вызове
        if not cls._proxy_list:
            cls._proxy_list = [p.strip() for p in settings.proxy_urls.split(",") if p.strip()]

        if not cls._proxy_list:
            return None

        # Ротация прокси
        proxy = cls._proxy_list[cls._proxy_index % len(cls._proxy_list)]
        cls._proxy_index += 1
        return proxy

    # Пул User-Agent'ов для ротации
    USER_AGENTS = [
        # Chrome Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        # Chrome Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Firefox Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        # Firefox Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
        # Safari Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        # Edge
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    ]

    async def search(self, filters: SearchFilters, limit: int = 50) -> list[Vacancy]:
        """Поиск вакансий на Avito с пагинацией"""
        vacancies = []
        settings = get_settings()
        has_proxy = bool(settings.proxy_urls)

        # Количество страниц для парсинга (больше с прокси)
        max_pages = 3 if has_proxy else 2
        items_per_page = 50  # Avito показывает ~50 на страницу

        city_slug = self._get_city_slug(filters.city)
        base_url = f"{self.base_url}/{city_slug}/vakansii"

        for page in range(1, max_pages + 1):
            # Если уже набрали достаточно — выходим
            if len(vacancies) >= limit:
                print(f"[Avito] Reached limit {limit}, stopping at page {page}")
                break

            try:
                page_vacancies = await self._fetch_page(
                    base_url, filters, page, has_proxy
                )

                if not page_vacancies:
                    print(f"[Avito] Page {page} returned 0 results, stopping")
                    break

                vacancies.extend(page_vacancies)
                print(f"[Avito] Page {page}: found {len(page_vacancies)}, total: {len(vacancies)}")

            except Exception as e:
                print(f"[Avito] Error on page {page}: {e}")
                break

        print(f"[Avito] Total vacancies found: {len(vacancies)}")
        return vacancies[:limit]

    async def _fetch_page(
        self, base_url: str, filters: SearchFilters, page: int, has_proxy: bool
    ) -> list[Vacancy]:
        """Загрузка одной страницы результатов"""
        import time

        # Rate limiting
        current_time = time.time()
        time_since_last = current_time - AvitoParser._last_request_time
        min_delay = 1.0 if has_proxy else 3.0
        random_extra = random.uniform(0.5, 1.5) if has_proxy else random.uniform(1.0, 3.0)

        if time_since_last < min_delay:
            wait_time = min_delay - time_since_last + random_extra
            print(f"[Avito] Rate limit: waiting {wait_time:.1f}s")
            await asyncio.sleep(wait_time)

        AvitoParser._last_request_time = time.time()
        AvitoParser._request_count += 1

        # Параметры запроса
        params = {}
        if filters.query:
            params["q"] = filters.query
        if page > 1:
            params["p"] = page
        if filters.salary_from:
            params["pmin"] = filters.salary_from
        # Сортировка по дате — свежие вакансии первыми
        params["s"] = 104  # 104 = по дате

        # Ротация User-Agent
        user_agent = self.USER_AGENTS[AvitoParser._request_count % len(self.USER_AGENTS)]
        print(f"[Avito] Page {page}, User-Agent #{AvitoParser._request_count % len(self.USER_AGENTS)}")

        # Варианты Accept-Language для разнообразия
        accept_languages = [
            "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "ru,en-US;q=0.9,en;q=0.8",
            "ru-RU,ru;q=0.9",
        ]

        # Реалистичные заголовки браузера с ротацией
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": random.choice(accept_languages),
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": str(random.randint(0, 1)),
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": random.choice(["max-age=0", "no-cache"]),
        }

        print(f"[Avito] Searching: {base_url} with params: {params}")

        # Получаем прокси (если настроены)
        proxy = self._get_proxy()
        if proxy:
            print(f"[Avito] Using proxy: {proxy[:20]}...")

        vacancies = []
        async with httpx.AsyncClient(proxy=proxy) as client:
            response = await client.get(
                base_url,
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

                city_slug = self._get_city_slug(filters.city)
                for item in items:
                    vacancy = self._parse_vacancy(item, filters.city or city_slug)
                    if vacancy and self.matches_filters(vacancy, filters):
                        vacancies.append(vacancy)

            elif response.status_code == 403:
                print("[Avito] Access denied (403) - Avito blocked the request")
            elif response.status_code == 429:
                print("[Avito] Rate limited (429) - backing off")
                backoff_time = random.uniform(10.0, 15.0)
                AvitoParser._last_request_time = time.time() + backoff_time
                print(f"[Avito] Next request delayed by {backoff_time:.1f}s")
                raise Exception("Rate limited")
            else:
                print(f"[Avito] Unexpected status: {response.status_code}")

        return vacancies

    def _get_city_slug(self, city: str) -> str:
        """Получить slug города для URL (автоматическая транслитерация)"""
        if not city:
            return "rossiya"
        city_lower = city.lower().strip()
        # Если передали "россия" — это вся страна
        if city_lower in ("россия", "russia", "рф", "rf", "rossiya"):
            return "rossiya"
        # Автоматическая транслитерация (Avito использует '_' как разделитель)
        return self.transliterate_city(city, separator="_")

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
