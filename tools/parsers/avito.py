"""
Парсер вакансий с Avito
Smart retry с ротацией сортировки и exponential backoff
"""

import httpx
import asyncio
import random
import re
import time
from typing import Optional
from bs4 import BeautifulSoup
from tools.parsers import BaseParser
from models.vacancy import Vacancy, SearchFilters
from config import get_settings


class AvitoParser(BaseParser):
    name = "avito"
    base_url = "https://www.avito.ru"
    _last_request_time = 0
    _request_count = 0
    _retry_count = 0  # Для exponential backoff
    _proxy_list: list[str] = []
    _proxy_index = 0

    # Варианты сортировки для retry
    SORT_OPTIONS = [
        104,  # По дате
        1,    # По умолчанию
        2,    # По цене ↑
        3,    # По цене ↓
    ]

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    ]

    @classmethod
    def _get_proxy(cls) -> Optional[str]:
        """Ротация прокси — каждый запрос новый прокси"""
        settings = get_settings()
        if not settings.proxy_urls:
            return None
        if not cls._proxy_list:
            cls._proxy_list = [p.strip() for p in settings.proxy_urls.split(",") if p.strip()]
            print(f"[Avito] Loaded {len(cls._proxy_list)} proxies")
        if not cls._proxy_list:
            return None
        # Всегда берём следующий прокси
        proxy = cls._proxy_list[cls._proxy_index % len(cls._proxy_list)]
        cls._proxy_index += 1
        print(f"[Avito] Using proxy #{cls._proxy_index % len(cls._proxy_list) + 1}/{len(cls._proxy_list)}")
        return proxy

    async def search(self, filters: SearchFilters, limit: int = 30) -> list[Vacancy]:
        """Быстрый поиск — одна попытка, без лишних retry"""
        settings = get_settings()
        has_proxy = bool(settings.proxy_urls)

        city_slug = self._get_city_slug(filters.city)
        base_url = f"{self.base_url}/{city_slug}/vakansii"

        # Один запрос с сортировкой по дате
        vacancies = await self._fetch_with_retry(base_url, filters, 104, has_proxy)

        # Если 0 — пробуем без сортировки
        if not vacancies:
            print("[Avito] Trying without sort...")
            vacancies = await self._fetch_with_retry(base_url, filters, None, has_proxy)

        print(f"[Avito] Total vacancies found: {len(vacancies)}")
        return vacancies[:limit]

    async def _fetch_with_retry(
        self, base_url: str, filters: SearchFilters, sort_option: int | None, has_proxy: bool
    ) -> list[Vacancy]:
        """Быстрый fetch — максимум 1 retry"""
        try:
            result = await self._fetch_page(base_url, filters, 1, sort_option, has_proxy)
            return result if result else []
        except Exception as e:
            print(f"[Avito] Error: {e}, retrying once...")
            await asyncio.sleep(2.0)
            try:
                return await self._fetch_page(base_url, filters, 1, sort_option, has_proxy) or []
            except Exception:
                return []

    async def _fetch_page(
        self, base_url: str, filters: SearchFilters, page: int, sort_option: int | None, has_proxy: bool
    ) -> list[Vacancy]:
        """Загрузка одной страницы"""
        # Минимальная задержка с прокси, больше без
        if not has_proxy:
            current_time = time.time()
            time_since_last = current_time - AvitoParser._last_request_time
            if time_since_last < 1.5:
                await asyncio.sleep(1.5 - time_since_last + random.uniform(0.2, 0.5))

        AvitoParser._last_request_time = time.time()
        AvitoParser._request_count += 1

        # Параметры
        params = {}
        if filters.query:
            params["q"] = filters.query
        if page > 1:
            params["p"] = page
        if filters.salary_from:
            params["pmin"] = filters.salary_from
        if sort_option:
            params["s"] = sort_option

        # Headers с ротацией
        user_agent = self.USER_AGENTS[AvitoParser._request_count % len(self.USER_AGENTS)]
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": random.choice([
                "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "ru,en-US;q=0.9,en;q=0.8",
                "ru-RU,ru;q=0.9",
            ]),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }

        print(f"[Avito] Fetching page {page}, sort={sort_option}, query='{filters.query}'")

        proxy = self._get_proxy()
        vacancies = []

        async with httpx.AsyncClient(proxy=proxy, timeout=25.0) as client:
            response = await client.get(
                base_url,
                params=params,
                headers=headers,
                follow_redirects=True,
            )

            print(f"[Avito] Status: {response.status_code}")

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                items = self._find_items(soup)

                city_slug = self._get_city_slug(filters.city)
                for item in items:
                    vacancy = self._parse_vacancy(item, filters.city or city_slug)
                    if vacancy and self.matches_filters(vacancy, filters):
                        vacancies.append(vacancy)

                print(f"[Avito] Page {page}: parsed {len(vacancies)} vacancies")

            elif response.status_code == 403:
                print("[Avito] 403 Forbidden - switching UA")
                AvitoParser._request_count += 5  # Пропускаем несколько UA
                raise Exception("403 Forbidden")

            elif response.status_code == 429:
                backoff = 15.0 * (2 ** AvitoParser._retry_count)
                print(f"[Avito] 429 Rate limited, backoff {backoff:.1f}s")
                AvitoParser._last_request_time = time.time() + backoff
                raise Exception("429 Rate limited")

        return vacancies

    def _find_items(self, soup: BeautifulSoup) -> list:
        """Поиск элементов вакансий разными селекторами"""
        selectors = [
            "[data-marker='item']",
            "[class*='iva-item']",
            "[itemtype*='JobPosting']",
            "div[class*='items-'] > div[class*='item']",
        ]

        for selector in selectors:
            items = soup.select(selector)
            if items:
                print(f"[Avito] Found {len(items)} items with '{selector}'")
                return items

        print("[Avito] No items found with any selector")
        return []

    def _get_city_slug(self, city: str) -> str:
        """Slug города для URL"""
        if not city:
            return "rossiya"
        city_lower = city.lower().strip()
        if city_lower in ("россия", "russia", "рф", "rf", "rossiya"):
            return "rossiya"
        return self.transliterate_city(city, separator="_")

    def _parse_vacancy(self, item, city: str) -> Optional[Vacancy]:
        """Парсинг вакансии из HTML"""
        try:
            # ID
            item_id = item.get("data-item-id", "") or item.get("id", "")
            if not item_id:
                link = item.select_one("a[href*='/vakansii/']")
                if link:
                    href = link.get("href", "")
                    match = re.search(r"_(\d+)$", href)
                    if match:
                        item_id = match.group(1)

            # Название
            title = ""
            for selector in ["[itemprop='name']", "h3[data-marker='item-title']", "[class*='title'] a", "a[title]", "a[href*='/vakansii/']"]:
                elem = item.select_one(selector)
                if elem:
                    title = elem.get_text(strip=True) or elem.get("title", "")
                    if title:
                        break

            if not title:
                return None

            # URL
            url = ""
            link_elem = item.select_one("a[itemprop='url']") or item.select_one("a[href*='/vakansii/']")
            if link_elem:
                href = link_elem.get("href", "")
                url = href if href.startswith("http") else self.base_url + href

            # Зарплата
            salary_from, salary_to = None, None
            for selector in ["[itemprop='price']", "[data-marker='item-price']", "[class*='price']"]:
                elem = item.select_one(selector)
                if elem:
                    price_text = elem.get("content", "") or elem.get_text(strip=True)
                    salary_from, salary_to = self._parse_salary(price_text)
                    if salary_from or salary_to:
                        break

            # Компания
            company = "Работодатель на Avito"
            company_elem = item.select_one("[data-marker='item-address']")
            if company_elem:
                company = company_elem.get_text(strip=True) or company

            # Описание
            description = ""
            for selector in ["[class*='description']", "[data-marker='item-description']", "[class*='snippet']"]:
                elem = item.select_one(selector)
                if elem:
                    description = elem.get_text(strip=True)[:500]
                    if description:
                        break

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
            print(f"[Avito] Parse error: {e}")
            return None

    def _parse_salary(self, text: str) -> tuple[Optional[int], Optional[int]]:
        """Парсинг зарплаты"""
        if not text:
            return None, None

        text = text.replace("\xa0", "").replace(" ", "").replace("₽", "").lower()
        salary_from, salary_to = None, None

        try:
            numbers = re.findall(r"\d+", text)
            if "от" in text and "до" in text and len(numbers) >= 2:
                salary_from, salary_to = int(numbers[0]), int(numbers[1])
            elif "от" in text and numbers:
                salary_from = int(numbers[0])
            elif "до" in text and numbers:
                salary_to = int(numbers[0])
            elif numbers:
                salary_from = int(numbers[0])
        except ValueError:
            pass

        return salary_from, salary_to
