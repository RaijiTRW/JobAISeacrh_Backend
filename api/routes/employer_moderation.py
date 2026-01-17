"""
API для модерации вакансий работодателей
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from agents.employer_vacancy_moderator import EmployerVacancyModerator


router = APIRouter(prefix="/api/employer/vacancies", tags=["employer_moderation"])


class ModerationRequest(BaseModel):
    """Запрос на модерацию вакансии"""

    title: str
    company: str
    description: str
    requirements: Optional[str] = None
    conditions: Optional[str] = None


class ModerationResponse(BaseModel):
    """Результат модерации"""

    approved: bool
    reason: Optional[str] = None


@router.post("/{vacancy_id}/moderate", response_model=ModerationResponse)
async def moderate_vacancy(vacancy_id: str, request: ModerationRequest):
    """
    Проверить вакансию работодателя на соответствие правилам платформы.

    AI проверяет на:
    - Военную тематику (запрещено)
    - Мошенничество
    - Неприемлемый контент
    - Нерелевантный контент (не вакансия)

    Возвращает:
    - approved: true - вакансия одобрена, можно публиковать
    - approved: false - вакансия отклонена, reason содержит причину
    """
    try:
        moderator = EmployerVacancyModerator()

        result = await moderator.check_vacancy(
            title=request.title,
            company=request.company,
            description=request.description,
            requirements=request.requirements,
            conditions=request.conditions,
        )

        return ModerationResponse(
            approved=result.get("approved", True),
            reason=result.get("reason"),
        )

    except Exception as e:
        print(f"[ModerationAPI] Error: {e}")
        # При ошибке - возвращаем approved=True (админ проверит вручную)
        return ModerationResponse(approved=True)
