from pydantic import BaseModel
from typing import Optional, Literal
from datetime import datetime


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    vacancies: list[dict] = []  # Вакансии если есть


class ChatRequest(BaseModel):
    message: str
    chat_id: Optional[str] = None
    user_id: str


class ChatResponse(BaseModel):
    message: str
    vacancies: list[dict] = []
    needs_clarification: bool = False
    clarification_questions: list[str] = []
    chat_id: str


class UserPreferences(BaseModel):
    """Собранные предпочтения пользователя"""
    query: Optional[str] = None
    city: Optional[str] = None
    salary_from: Optional[int] = None
    salary_to: Optional[int] = None
    experience: Optional[str] = None
    employment_type: Optional[str] = None
    exclude_keywords: list[str] = []

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
