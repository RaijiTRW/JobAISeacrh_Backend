from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class Vacancy(BaseModel):
    id: str
    title: str
    company: str
    salary_from: Optional[int] = None
    salary_to: Optional[int] = None
    salary_currency: str = "RUB"
    city: str
    experience: Optional[str] = None
    employment_type: Optional[str] = None  # full, part, remote
    description: str
    url: str
    source: str  # hh, avito, superjob, platform
    published_at: Optional[datetime] = None
    user_id: Optional[str] = None  # ID владельца (для platform вакансий)

    @property
    def salary_display(self) -> str:
        if self.salary_from and self.salary_to:
            return f"{self.salary_from:,} - {self.salary_to:,} ₽".replace(",", " ")
        elif self.salary_from:
            return f"от {self.salary_from:,} ₽".replace(",", " ")
        elif self.salary_to:
            return f"до {self.salary_to:,} ₽".replace(",", " ")
        return "Не указана"


class SearchFilters(BaseModel):
    query: Optional[str] = None        # Для парсеров (один запрос)
    queries: list[str] = []            # Для tool (массив от ИИ)
    city: Optional[str] = None
    salary_from: Optional[int] = None
    salary_to: Optional[int] = None
    experience: Optional[str] = None  # no_experience, 1-3, 3-6, 6+
    employment_type: Optional[str] = None  # full, part, remote
    exclude_keywords: list[str] = []
    exclude_vacancy_ids: list[str] = []  # ID вакансий для исключения
    # Режим поиска
    search_in_feed: bool = True   # Поиск в ленте (БД)
    search_online: bool = True    # Поиск в сети (live)


class SearchResult(BaseModel):
    vacancies: list[Vacancy]
    total_found: int
    filters_applied: SearchFilters
