from abc import ABC, abstractmethod
from app.models.vacancy import Vacancy, SearchFilters


class BaseParser(ABC):
    """Базовый класс для парсеров вакансий"""

    name: str = "base"

    @abstractmethod
    async def search(self, filters: SearchFilters, limit: int = 20) -> list[Vacancy]:
        """Поиск вакансий по фильтрам"""
        pass

    def matches_filters(self, vacancy: Vacancy, filters: SearchFilters) -> bool:
        """Проверка соответствия вакансии фильтрам"""
        # Проверка исключающих слов
        if filters.exclude_keywords:
            text = f"{vacancy.title} {vacancy.description}".lower()
            for keyword in filters.exclude_keywords:
                if keyword.lower() in text:
                    return False

        # Проверка зарплаты
        if filters.salary_from and vacancy.salary_to:
            if vacancy.salary_to < filters.salary_from:
                return False

        return True
