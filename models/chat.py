from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    vacancies: list[dict] = Field(default_factory=list)  # Вакансии если есть


class LifestylePreferences(BaseModel):
    """Предпочтения по стилю работы в AI-чате."""
    full_remote_only: bool = False
    no_mandatory_calls: bool = False
    async_first: bool = False
    flexible_hours: bool = False
    strict_mode: bool = False


class ChatRequest(BaseModel):
    message: str
    chat_id: Optional[str] = None
    user_id: str
    search_in_feed: bool = True   # Поиск в ленте (БД)
    search_online: bool = True    # Поиск в сети (live)
    exclude_vacancy_ids: list[str] = Field(default_factory=list)
    lifestyle_preferences: Optional[LifestylePreferences] = None


class ChatResponse(BaseModel):
    message: str
    vacancies: list[dict] = Field(default_factory=list)
    rejected_vacancies: list[dict] = Field(default_factory=list)  # Отсеянные вакансии
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    chat_id: str


class UserPreferences(BaseModel):
    """Собранные предпочтения пользователя"""
    query: Optional[str] = None
    city: Optional[str] = None
    salary_from: Optional[int] = None
    salary_to: Optional[int] = None
    experience: Optional[str] = None
    employment_type: Optional[str] = None
    exclude_keywords: list[str] = Field(default_factory=list)
    asked_salary: bool = False  # Флаг: спрашивали ли про зарплату

    def is_complete(self) -> bool:
        """Достаточно ли данных для поиска"""
        return bool(self.query and self.city)

    def to_filters(self):
        from models.vacancy import SearchFilters
        return SearchFilters(
            query=self.query or "",
            city=self.city,
            salary_from=self.salary_from,
            salary_to=self.salary_to,
            experience=self.experience,
            employment_type=self.employment_type,
            exclude_keywords=self.exclude_keywords,
        )
