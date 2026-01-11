"""
Модели для ленты вакансий
"""
from typing import Optional
from pydantic import BaseModel, Field
from models.vacancy import Vacancy


class FeedFilters(BaseModel):
    """Фильтры для ленты вакансий"""
    query: Optional[str] = None
    city: Optional[str] = None
    salary_from: Optional[int] = None
    experience: Optional[str] = None  # no_experience, 1-3, 3-6, 6+
    sort: str = Field(default="date", description="date, salary_desc, salary_asc, relevance")
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


class FeedResult(BaseModel):
    """Результат запроса ленты"""
    vacancies: list[Vacancy]
    total: int
    page: int
    pages: int
    has_next: bool
