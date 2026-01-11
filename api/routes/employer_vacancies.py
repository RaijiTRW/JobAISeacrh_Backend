"""
API роутер для вакансий работодателей
"""
from fastapi import APIRouter, HTTPException, Header, Query
from typing import Optional

from models.employer_vacancy import (
    EmployerVacancy,
    EmployerVacancyCreate,
    EmployerVacancyUpdate,
    EmployerVacancyList,
)
from services.employer_vacancy_service import employer_vacancy_service
from services.auth_service import get_user_from_token

router = APIRouter(prefix="/api/employer/vacancies", tags=["employer-vacancies"])


def get_user_id_and_token(authorization: str) -> tuple[str, str]:
    """Извлечь user_id и token из заголовка Authorization"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    token = authorization.replace("Bearer ", "")
    user = get_user_from_token(token)

    if not user:
        raise HTTPException(status_code=401, detail="Недействительный токен")

    return user.get("sub") or user.get("id"), token


@router.post("", response_model=EmployerVacancy)
async def create_vacancy(
    vacancy: EmployerVacancyCreate,
    authorization: str = Header(...),
):
    """Создать новую вакансию"""
    user_id, token = get_user_id_and_token(authorization)

    result = await employer_vacancy_service.create(vacancy, user_id, token)
    if not result:
        raise HTTPException(status_code=500, detail="Не удалось создать вакансию")

    return result


@router.get("", response_model=EmployerVacancyList)
async def get_my_vacancies(
    authorization: str = Header(...),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Получить свои вакансии"""
    user_id, token = get_user_id_and_token(authorization)

    return await employer_vacancy_service.get_user_vacancies(user_id, token, page, limit)


@router.get("/{vacancy_id}", response_model=EmployerVacancy)
async def get_vacancy(
    vacancy_id: str,
    authorization: Optional[str] = Header(None),
):
    """Получить вакансию по ID"""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    vacancy = await employer_vacancy_service.get_by_id(vacancy_id, token)
    if not vacancy:
        raise HTTPException(status_code=404, detail="Вакансия не найдена")

    return vacancy


@router.put("/{vacancy_id}", response_model=EmployerVacancy)
async def update_vacancy(
    vacancy_id: str,
    updates: EmployerVacancyUpdate,
    authorization: str = Header(...),
):
    """Обновить вакансию"""
    user_id, token = get_user_id_and_token(authorization)

    # Проверяем владельца
    existing = await employer_vacancy_service.get_by_id(vacancy_id, token)
    if not existing:
        raise HTTPException(status_code=404, detail="Вакансия не найдена")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой вакансии")

    result = await employer_vacancy_service.update(vacancy_id, updates, token)
    if not result:
        raise HTTPException(status_code=500, detail="Не удалось обновить вакансию")

    return result


@router.delete("/{vacancy_id}")
async def delete_vacancy(
    vacancy_id: str,
    authorization: str = Header(...),
):
    """Удалить вакансию"""
    user_id, token = get_user_id_and_token(authorization)

    # Проверяем владельца
    existing = await employer_vacancy_service.get_by_id(vacancy_id, token)
    if not existing:
        raise HTTPException(status_code=404, detail="Вакансия не найдена")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой вакансии")

    success = await employer_vacancy_service.delete(vacancy_id, token)
    if not success:
        raise HTTPException(status_code=500, detail="Не удалось удалить вакансию")

    return {"success": True}


@router.post("/{vacancy_id}/publish", response_model=EmployerVacancy)
async def publish_vacancy(
    vacancy_id: str,
    authorization: str = Header(...),
):
    """Опубликовать вакансию"""
    user_id, token = get_user_id_and_token(authorization)

    # Проверяем владельца
    existing = await employer_vacancy_service.get_by_id(vacancy_id, token)
    if not existing:
        raise HTTPException(status_code=404, detail="Вакансия не найдена")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой вакансии")

    result = await employer_vacancy_service.publish(vacancy_id, token)
    if not result:
        raise HTTPException(status_code=500, detail="Не удалось опубликовать вакансию")

    return result


@router.post("/{vacancy_id}/close", response_model=EmployerVacancy)
async def close_vacancy(
    vacancy_id: str,
    authorization: str = Header(...),
):
    """Закрыть вакансию"""
    user_id, token = get_user_id_and_token(authorization)

    # Проверяем владельца
    existing = await employer_vacancy_service.get_by_id(vacancy_id, token)
    if not existing:
        raise HTTPException(status_code=404, detail="Вакансия не найдена")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой вакансии")

    result = await employer_vacancy_service.close(vacancy_id, token)
    if not result:
        raise HTTPException(status_code=500, detail="Не удалось закрыть вакансию")

    return result
