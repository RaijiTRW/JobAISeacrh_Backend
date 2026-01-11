"""
Модели для вакансий от работодателей
"""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class EmployerVacancyCreate(BaseModel):
    """Создание вакансии"""
    title: str = Field(..., min_length=3, max_length=200)
    company: str = Field(..., min_length=2, max_length=200)
    city: str = Field(..., min_length=2, max_length=100)

    salary_from: Optional[int] = None
    salary_to: Optional[int] = None
    salary_currency: str = "RUB"

    experience: Optional[str] = None  # no_experience, 1-3, 3-6, 6+
    employment_type: Optional[str] = None  # full, part, remote
    schedule: Optional[str] = None  # full_day, flexible, shift

    description: str = Field(..., min_length=50, max_length=10000)
    requirements: Optional[str] = None
    conditions: Optional[str] = None

    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None


class EmployerVacancyUpdate(BaseModel):
    """Обновление вакансии"""
    title: Optional[str] = Field(None, min_length=3, max_length=200)
    company: Optional[str] = Field(None, min_length=2, max_length=200)
    city: Optional[str] = Field(None, min_length=2, max_length=100)

    salary_from: Optional[int] = None
    salary_to: Optional[int] = None
    salary_currency: Optional[str] = None

    experience: Optional[str] = None
    employment_type: Optional[str] = None
    schedule: Optional[str] = None

    description: Optional[str] = Field(None, min_length=50, max_length=10000)
    requirements: Optional[str] = None
    conditions: Optional[str] = None

    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None


class EmployerVacancy(BaseModel):
    """Вакансия от работодателя"""
    id: str
    user_id: str

    title: str
    company: str
    city: str

    salary_from: Optional[int] = None
    salary_to: Optional[int] = None
    salary_currency: str = "RUB"

    experience: Optional[str] = None
    employment_type: Optional[str] = None
    schedule: Optional[str] = None

    description: str
    requirements: Optional[str] = None
    conditions: Optional[str] = None

    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None

    status: str = "draft"  # draft, published, closed
    is_active: bool = True
    views_count: int = 0
    responses_count: int = 0

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class EmployerVacancyList(BaseModel):
    """Список вакансий работодателя"""
    vacancies: list[EmployerVacancy]
    total: int
    page: int
    pages: int
    has_next: bool


class VacancyStats(BaseModel):
    """Статистика вакансий"""
    platform: int  # Наши вакансии
    network: int   # Вакансии из сети (hh, avito, superjob)
    total: int     # Всего
