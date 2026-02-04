"""
Парсер вакансий с SuperJob
Использует веб-скрапинг
ВАЖНО: SuperJob может блокировать запросы, парсер может работать нестабильно
"""

import asyncio
import httpx
import re
import ssl
from typing import Optional
from bs4 import BeautifulSoup
from tools.parsers import BaseParser
from models.vacancy import Vacancy, SearchFilters


class SuperJobParser(BaseParser):
    name = "superjob"
    base_url = "https://www.superjob.ru"

    async def search(self, filters: SearchFilters, limit: int = 30) -> list[Vacancy]:
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

            # Тип занятости (для удаленной работы)
            if filters.employment_type == "remote":
                params["catalogues"] = "34"  # Категория "Удаленная работа" в SuperJob

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

            # Попытка 1: httpx с verify=False
            response = None
            try:
                limits = httpx.Limits(max_keepalive_connections=1, max_connections=1)
                async with httpx.AsyncClient(
                    verify=False,
                    timeout=20.0,
                    limits=limits,
                    follow_redirects=True,
                ) as client:
                    response = await client.get(url, params=params, headers=headers)
            except (httpx.RemoteProtocolError, ssl.SSLError) as e1:
                print(f"[SuperJob] Attempt 1 failed: {type(e1).__name__}")

                # Попытка 2: httpx с custom SSL context
                try:
                    import ssl as ssl_module
                    ssl_context = ssl_module.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl_module.CERT_NONE

                    limits = httpx.Limits(max_keepalive_connections=1, max_connections=1)
                    async with httpx.AsyncClient(
                        verify=ssl_context,
                        timeout=20.0,
                        limits=limits,
                        follow_redirects=True,
                    ) as client:
                        response = await client.get(url, params=params, headers=headers)
                    print(f"[SuperJob] Attempt 2 succeeded")
                except Exception as e2:
                    print(f"[SuperJob] Attempt 2 failed: {type(e2).__name__}")

                    # Попытка 3: используем sync requests в async wrapper
                    try:
                        import requests
                        from concurrent.futures import ThreadPoolExecutor
                        import urllib3

                        def make_request():
                            # Отключаем предупреждения SSL
                            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

                            session = requests.Session()
                            session.verify = False
                            session.headers.update(headers)

                            resp = session.get(url, params=params, timeout=20, allow_redirects=True)
                            return resp

                        # Получаем текущий loop или создаём новый
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = asyncio.get_event_loop()

                        with ThreadPoolExecutor() as pool:
                            resp = await loop.run_in_executor(pool, make_request)

                        # Convert requests.Response to httpx-like response
                        class HTTPXLikeResponse:
                            def __init__(self, requests_resp):
                                self._resp = requests_resp
                                self.status_code = requests_resp.status_code
                                self.text = requests_resp.text

                            def raise_for_status(self):
                                self._resp.raise_for_status()
                                return self

                        response = HTTPXLikeResponse(resp)
                        print(f"[SuperJob] Attempt 3 (requests) succeeded with status {response.status_code}")

                    except Exception as e3:
                        print(f"[SuperJob] All attempts failed: {type(e3).__name__}")
                        return vacancies
            except Exception as e:
                # Неожиданные ошибки (не SSL)
                print(f"[SuperJob] Request error: {type(e).__name__}: {e}")
                return vacancies

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

        print(f"[SuperJob] Total vacancies found: {len(vacancies)}")
        return vacancies

    # Расширенный маппинг городов на ID в SuperJob (~100 городов)
    CITY_IDS = {
        # Миллионники
        "москва": "4",
        "санкт-петербург": "2",
        "спб": "2",
        "питер": "2",
        "новосибирск": "13",
        "екатеринбург": "14",
        "казань": "34",
        "нижний новгород": "22",
        "челябинск": "24",
        "омск": "20",
        "самара": "19",
        "ростов-на-дону": "15",
        "уфа": "18",
        "красноярск": "16",
        "воронеж": "21",
        "пермь": "23",
        "волгоград": "17",
        "краснодар": "12",
        # Крупные (500K-1M)
        "саратов": "35",
        "тюмень": "36",
        "тольятти": "37",
        "ижевск": "38",
        "барнаул": "39",
        "иркутск": "41",
        "ульяновск": "42",
        "хабаровск": "43",
        "владивосток": "44",
        "ярославль": "45",
        "махачкала": "46",
        "томск": "47",
        "оренбург": "48",
        "кемерово": "49",
        "новокузнецк": "50",
        "рязань": "51",
        "набережные челны": "52",
        "астрахань": "53",
        "пенза": "54",
        "липецк": "56",
        "киров": "57",
        # Средние (200K-500K)
        "калининград": "58",
        "чебоксары": "59",
        "тула": "60",
        "курск": "61",
        "сочи": "55",
        "ставрополь": "62",
        "улан-удэ": "63",
        "тверь": "64",
        "магнитогорск": "65",
        "брянск": "66",
        "белгород": "67",
        "иваново": "68",
        "сургут": "69",
        "владимир": "70",
        "нижний тагил": "71",
        "архангельск": "72",
        "чита": "73",
        "симферополь": "74",
        "калуга": "75",
        "смоленск": "76",
        "волжский": "77",
        "саранск": "78",
        "курган": "79",
        "череповец": "80",
        "вологда": "81",
        "орёл": "82",
        "владикавказ": "83",
        "якутск": "84",
        "грозный": "85",
        "мурманск": "86",
        "тамбов": "87",
        "стерлитамак": "88",
        "кострома": "89",
        "петрозаводск": "90",
        "нижневартовск": "91",
        "йошкар-ола": "92",
        "таганрог": "93",
        "новороссийск": "961",
        "комсомольск-на-амуре": "94",
        "нальчик": "95",
        "сыктывкар": "96",
        # Средние (100K-200K)
        "шахты": "97",
        "дзержинск": "98",
        "орск": "99",
        "братск": "100",
        "ангарск": "101",
        "энгельс": "102",
        "благовещенск": "103",
        "старый оскол": "104",
        "великий новгород": "105",
        "псков": "106",
        "бийск": "107",
        "южно-сахалинск": "108",
        "рыбинск": "109",
        "армавир": "110",
        "северодвинск": "111",
        "абакан": "112",
        "петропавловск-камчатский": "113",
        "норильск": "114",
        "балаково": "115",
        "златоуст": "116",
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
