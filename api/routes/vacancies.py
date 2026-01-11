"""
API эндпоинты для ленты вакансий
"""
from typing import Optional
from fastapi import APIRouter, Query

from models.feed import FeedFilters, FeedResult
from models.employer_vacancy import VacancyStats
from services.vacancy_feed import vacancy_feed_service
from services.vacancy_storage import vacancy_storage_service
from services.employer_vacancy_service import employer_vacancy_service

router = APIRouter(tags=["vacancies"])


@router.get("/feed", response_model=FeedResult)
async def get_vacancy_feed(
    query: Optional[str] = Query(None, description="Поисковый запрос"),
    city: Optional[str] = Query(None, description="Город"),
    salary_from: Optional[int] = Query(None, description="Минимальная зарплата"),
    experience: Optional[str] = Query(None, description="Опыт: no_experience, 1-3, 3-6, 6+"),
    source: Optional[str] = Query(None, description="Источник: platform, network"),
    sort: str = Query("date", description="Сортировка: date, salary_desc, salary_asc, relevance"),
    page: int = Query(1, ge=1, description="Номер страницы"),
    limit: int = Query(20, ge=1, le=100, description="Количество на страницу"),
):
    """
    Получить ленту вакансий с фильтрацией и пагинацией.

    Агрегирует вакансии с hh.ru, Avito и SuperJob.
    Автоматически удаляет дубликаты.
    """
    filters = FeedFilters(
        query=query,
        city=city,
        salary_from=salary_from,
        experience=experience,
        source=source,
        sort=sort,
        page=page,
        limit=limit,
    )

    return await vacancy_feed_service.get_feed(filters)


@router.get("/health")
async def vacancies_health():
    """Health check для API вакансий"""
    return {"status": "ok", "service": "vacancies"}


@router.get("/stats", response_model=VacancyStats)
async def get_vacancy_stats():
    """
    Получить статистику вакансий:
    - platform: наши вакансии (созданы на платформе)
    - network: вакансии из сети (hh, avito, superjob)
    - total: всего
    """
    # Наши вакансии (опубликованные)
    platform_count = await employer_vacancy_service.get_published_count()

    # Вакансии из сети (из хранилища)
    network_stats = await vacancy_storage_service.get_stats()
    network_count = network_stats.get("total", 0)

    return VacancyStats(
        platform=platform_count,
        network=network_count,
        total=platform_count + network_count,
    )
