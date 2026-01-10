"""
Парсер вакансий с SuperJob
Использует веб-скрапинг
ВАЖНО: SuperJob может блокировать запросы, парсер может работать нестабильно
"""

import httpx
import re
from typing import Optional
from bs4 import BeautifulSoup
from tools.parsers import BaseParser
from models.vacancy import Vacancy, SearchFilters


class SuperJobParser(BaseParser):
    name = "superjob"
    base_url = "https://www.superjob.ru"

    async def search(self, filters: SearchFilters, limit: int = 20) -> list[Vacancy]:
        """Поиск вакансий на SuperJob"""
        vacancies = []

        try:
            # SuperJob использует geo[t][0]=ID для города
            url = f"{self.base_url}/vakansii/"

            params = {}
            if filters.query:
                params["keywords"] = filters.query

            # Добавляем город по ID
            city_id = self._get_city_id(filters.city)
            if city_id:
                params["geo[t][0]"] = city_id

            # Фильтр зарплаты
            if filters.salary_from:
                params["payment_from"] = filters.salary_from

            # Опыт работы
            if filters.experience:
                exp_map = {
                    "no_experience": "0",
                    "1-3": "1",
                    "3-6": "2",
                    "6+": "3",
                }
                if filters.experience in exp_map:
                    params["experience"] = exp_map[filters.experience]

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
            }

            print(f"[SuperJob] Searching: {url} with params: {params}")

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=20.0,
                    follow_redirects=True,
                )

                print(f"[SuperJob] Response status: {response.status_code}")

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")

                    # SuperJob использует разные классы, пробуем несколько селекторов
                    items = []

                    # Основной селектор
                    items = soup.select("[class*='f-test-vacancy-item']")
                    print(f"[SuperJob] Found {len(items)} items with f-test-vacancy-item")

                    # Альтернативные селекторы
                    if not items:
                        items = soup.select("[class*='VacancyItem']")
                        print(f"[SuperJob] Found {len(items)} items with VacancyItem")

                    if not items:
                        items = soup.select("[class*='_vacancy']")
                        print(f"[SuperJob] Found {len(items)} items with _vacancy")

                    if not items:
                        # Пробуем найти по ссылкам на вакансии
                        vacancy_links = soup.select("a[href*='/vakansii/'][href$='.html']")
                        print(f"[SuperJob] Found {len(vacancy_links)} vacancy links")
                        # Получаем родительские контейнеры
                        seen_parents = set()
                        for link in vacancy_links:
                            parent = link.find_parent("div", recursive=True)
                            if parent and id(parent) not in seen_parents:
                                seen_parents.add(id(parent))
                                items.append(parent)

                    items = items[:limit]

                    for item in items:
                        vacancy = self._parse_vacancy(item, filters.city or "Россия")
                        if vacancy and self.matches_filters(vacancy, filters):
                            vacancies.append(vacancy)

                elif response.status_code == 403:
                    print("[SuperJob] Access denied (403) - SuperJob blocked the request")
                elif response.status_code == 429:
                    print("[SuperJob] Rate limited (429) - too many requests")
                else:
                    print(f"[SuperJob] Unexpected status: {response.status_code}")

        except httpx.TimeoutException:
            print("[SuperJob] Request timeout")
        except Exception as e:
            print(f"[SuperJob] Parser error: {e}")
            import traceback
            traceback.print_exc()

        print(f"[SuperJob] Total vacancies found: {len(vacancies)}")
        return vacancies

    # Маппинг городов на ID в SuperJob
    CITY_IDS = {
        "москва": "4",
        "санкт-петербург": "2",
        "спб": "2",
        "питер": "2",
        "новосибирск": "13",
        "екатеринбург": "14",
        "нижний новгород": "22",
        "казань": "34",
        "краснодар": "12",
        "ростов-на-дону": "15",
        "новороссийск": "961",
        "сочи": "55",
        "воронеж": "21",
        "пермь": "23",
        "волгоград": "17",
        "самара": "19",
        "уфа": "18",
        "красноярск": "16",
        "омск": "20",
        "челябинск": "24",
    }

    def _get_city_id(self, city: str) -> Optional[str]:
        """Получить ID города для SuperJob"""
        if not city:
            return None
        city_lower = city.lower().strip()
        if city_lower in ("россия", "russia", "рф", "rf", "rossiya"):
            return None
        return self.CITY_IDS.get(city_lower)

    def _parse_vacancy(self, item, city: str) -> Optional[Vacancy]:
        """Парсинг вакансии из HTML"""
        try:
            # Название и ссылка - пробуем разные селекторы
            title = ""
            url = ""

            link_selectors = [
                "a[href*='/vakansii/'][href$='.html']",
                "a[href*='/vakansii/']",
                "[class*='title'] a",
                "h3 a",
                "h2 a",
            ]

            for selector in link_selectors:
                title_elem = item.select_one(selector)
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    url = title_elem.get("href", "")
                    if url and not url.startswith("http"):
                        url = self.base_url + url
                    if title:
                        break

            if not title:
                return None

            # ID из URL
            item_id = ""
            if url:
                match = re.search(r"-(\d+)\.html", url)
                if match:
                    item_id = match.group(1)
                else:
                    item_id = str(hash(url))

            # Компания
            company = "Компания на SuperJob"
            company_selectors = [
                "[class*='company']",
                "[class*='Company']",
                "[class*='employer']",
            ]
            for selector in company_selectors:
                company_elem = item.select_one(selector)
                if company_elem:
                    company_text = company_elem.get_text(strip=True)
                    if company_text and len(company_text) < 100:
                        company = company_text
                        break

            # Зарплата
            salary_from = None
            salary_to = None
            salary_selectors = [
                "[class*='salary']",
                "[class*='Salary']",
                "[class*='payment']",
                "[class*='Price']",
            ]
            for selector in salary_selectors:
                salary_elem = item.select_one(selector)
                if salary_elem:
                    salary_from, salary_to = self._parse_salary(salary_elem)
                    if salary_from or salary_to:
                        break

            # Описание
            description = ""
            desc_selectors = [
                "[class*='description']",
                "[class*='Description']",
                "[class*='snippet']",
                "[class*='preview']",
            ]
            for selector in desc_selectors:
                desc_elem = item.select_one(selector)
                if desc_elem:
                    description = desc_elem.get_text(strip=True)[:500]
                    if description:
                        break

            return Vacancy(
                id=f"sj_{item_id}",
                title=title,
                company=company,
                salary_from=salary_from,
                salary_to=salary_to,
                city=city.title() if city else "Россия",
                description=description,
                url=url,
                source="superjob",
            )

        except Exception as e:
            print(f"[SuperJob] Error parsing vacancy: {e}")
            return None

    def _parse_salary(self, elem) -> tuple[Optional[int], Optional[int]]:
        """Парсинг зарплаты из элемента"""
        if not elem:
            return None, None

        text = elem.get_text(strip=True).lower()

        # Убираем пробелы в числах и символы валюты
        text = text.replace("\xa0", "").replace(" ", "").replace("₽", "").replace("руб", "")

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
            elif len(numbers) == 2 and "—" in text or "-" in text:
                salary_from = int(numbers[0])
                salary_to = int(numbers[1])
            elif numbers:
                salary_from = int(numbers[0])
        except ValueError:
            pass

        return salary_from, salary_to
