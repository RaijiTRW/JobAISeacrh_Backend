"""
Парсер вакансий с SuperJob
Использует веб-скрапинг
"""

import httpx
from typing import Optional
from bs4 import BeautifulSoup
from app.tools.parsers import BaseParser
from app.models.vacancy import Vacancy, SearchFilters


class SuperJobParser(BaseParser):
    name = "superjob"
    base_url = "https://www.superjob.ru"

    # Маппинг городов
    CITY_SLUGS = {
        "москва": "moskva",
        "санкт-петербург": "sankt-peterburg",
        "спб": "sankt-peterburg",
        "питер": "sankt-peterburg",
        "новосибирск": "novosibirsk",
        "екатеринбург": "ekaterinburg",
        "казань": "kazan",
        "нижний новгород": "nizhnij-novgorod",
        "краснодар": "krasnodar",
        "ростов-на-дону": "rostov-na-donu",
        "воронеж": "voronezh",
        "пермь": "perm",
    }

    async def search(self, filters: SearchFilters, limit: int = 20) -> list[Vacancy]:
        """Поиск вакансий на SuperJob"""
        vacancies = []

        try:
            city_slug = self._get_city_slug(filters.city) if filters.city else ""
            url = f"{self.base_url}/vakansii"
            if city_slug:
                url = f"{self.base_url}/vakansii/{city_slug}"

            params = {
                "keywords": filters.query,
            }

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

                    # SuperJob использует разные классы, ищем по структуре
                    items = soup.select("[class*='f-test-vacancy-item']")[:limit]

                    if not items:
                        # Альтернативный селектор
                        items = soup.select("[class*='VacancyItem']")[:limit]

                    for item in items:
                        vacancy = self._parse_vacancy(item, filters.city or "Россия")
                        if vacancy and self.matches_filters(vacancy, filters):
                            vacancies.append(vacancy)

        except Exception as e:
            print(f"SuperJob Parser error: {e}")

        return vacancies

    def _get_city_slug(self, city: str) -> str:
        """Получить slug города для URL"""
        city_lower = city.lower()
        return self.CITY_SLUGS.get(city_lower, city_lower.replace(" ", "-"))

    def _parse_vacancy(self, item, city: str) -> Optional[Vacancy]:
        """Парсинг вакансии из HTML"""
        try:
            # Название и ссылка
            title_elem = item.select_one("a[href*='/vakansii/']")
            if not title_elem:
                return None

            title = title_elem.get_text(strip=True)
            url = title_elem.get("href", "")
            if url and not url.startswith("http"):
                url = self.base_url + url

            # ID из URL
            item_id = url.split("-")[-1].split(".")[0] if url else ""

            # Компания
            company_elem = item.select_one("[class*='company'], [class*='Company']")
            company = company_elem.get_text(strip=True) if company_elem else "Компания на SuperJob"

            # Зарплата
            salary_elem = item.select_one("[class*='salary'], [class*='Salary']")
            salary_from, salary_to = self._parse_salary(salary_elem)

            # Описание
            desc_elem = item.select_one("[class*='description'], [class*='Description']")
            description = desc_elem.get_text(strip=True)[:500] if desc_elem else ""

            if not title or not item_id:
                return None

            return Vacancy(
                id=f"sj_{item_id}",
                title=title,
                company=company,
                salary_from=salary_from,
                salary_to=salary_to,
                city=city.title(),
                description=description,
                url=url,
                source="superjob",
            )

        except Exception as e:
            print(f"Error parsing SuperJob vacancy: {e}")
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
            if "от" in text and "до" in text:
                parts = text.split("до")
                salary_from = int("".join(filter(str.isdigit, parts[0])))
                salary_to = int("".join(filter(str.isdigit, parts[1])))
            elif "от" in text:
                salary_from = int("".join(filter(str.isdigit, text)))
            elif "до" in text:
                salary_to = int("".join(filter(str.isdigit, text)))
            elif text.replace("-", "").isdigit():
                if "-" in text:
                    parts = text.split("-")
                    salary_from = int(parts[0])
                    salary_to = int(parts[1])
                else:
                    salary_from = int(text)
        except ValueError:
            pass

        return salary_from, salary_to
