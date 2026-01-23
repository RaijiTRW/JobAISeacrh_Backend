"""
CrewAI Tools for vacancy search.
"""
import asyncio
from typing import Optional, Any
from crewai_tools import BaseTool
from pydantic import Field


class VacancySearchTool(BaseTool):
    """
    Tool for searching vacancies on HH.ru, Avito, and SuperJob.
    Searches both database and live sources.
    """

    name: str = "vacancy_search"
    description: str = """
    Поиск вакансий на HH.ru, Avito и SuperJob.

    Параметры:
    - queries: список поисковых запросов (3-8 вариантов)
    - city: город поиска (обязательно)
    - salary_from: минимальная зарплата (опционально)
    - experience: опыт работы - no_experience, 1-3, 3-6, 6+ (опционально)
    - employment_type: тип занятости - full, part, remote (опционально)
    - exclude_keywords: слова для исключения (опционально)

    Возвращает список вакансий с полной информацией.
    """

    def _run(
        self,
        queries: list[str],
        city: str,
        salary_from: Optional[int] = None,
        experience: Optional[str] = None,
        employment_type: Optional[str] = None,
        exclude_keywords: Optional[list[str]] = None,
    ) -> list[dict]:
        """Execute vacancy search."""
        from tools.search import vacancy_search
        from models.vacancy import SearchFilters

        filters = SearchFilters(
            queries=queries,
            city=city,
            salary_from=salary_from,
            experience=experience,
            employment_type=employment_type,
            exclude_keywords=exclude_keywords or [],
            search_in_feed=True,
            search_online=True,
        )

        # Run async search in sync context
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If already in async context, create a new task
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, vacancy_search.search(filters))
                    result = future.result(timeout=120)
            else:
                result = asyncio.run(vacancy_search.search(filters))
        except Exception as e:
            print(f"[VacancySearchTool] Error: {e}")
            return []

        # Convert vacancies to dict
        return [v.model_dump(mode='json') for v in result.vacancies]


class DatabaseSearchTool(BaseTool):
    """
    Tool for fast database-only vacancy search.
    Searches only cached vacancies in the database.
    """

    name: str = "database_search"
    description: str = """
    Быстрый поиск вакансий ТОЛЬКО в базе данных (кэшированные вакансии).
    Используй когда нужен быстрый результат без ожидания live-поиска.

    Параметры те же что у vacancy_search.
    """

    def _run(
        self,
        queries: list[str],
        city: str,
        salary_from: Optional[int] = None,
        experience: Optional[str] = None,
        employment_type: Optional[str] = None,
        exclude_keywords: Optional[list[str]] = None,
    ) -> list[dict]:
        """Execute database-only vacancy search."""
        from tools.search import vacancy_search
        from models.vacancy import SearchFilters

        filters = SearchFilters(
            queries=queries,
            city=city,
            salary_from=salary_from,
            experience=experience,
            employment_type=employment_type,
            exclude_keywords=exclude_keywords or [],
            search_in_feed=True,
            search_online=False,  # Only database
        )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, vacancy_search.search(filters))
                    result = future.result(timeout=60)
            else:
                result = asyncio.run(vacancy_search.search(filters))
        except Exception as e:
            print(f"[DatabaseSearchTool] Error: {e}")
            return []

        return [v.model_dump(mode='json') for v in result.vacancies]


# Tool instances
vacancy_search_tool = VacancySearchTool()
database_search_tool = DatabaseSearchTool()
