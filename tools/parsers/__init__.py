from abc import ABC, abstractmethod
from models.vacancy import Vacancy, SearchFilters


# Таблица транслитерации русский → латиница
TRANSLIT_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}

# Особые случаи для городов (исключения из правил транслитерации)
CITY_SPECIAL_CASES = {
    "санкт-петербург": "sankt-peterburg",
    "спб": "sankt-peterburg",
    "питер": "sankt-peterburg",
    "нижний новгород": "nizhniy_novgorod",
    "ростов-на-дону": "rostov-na-donu",
    "набережные челны": "naberezhnye_chelny",
    "великий новгород": "velikiy_novgorod",
    "йошкар-ола": "yoshkar-ola",
}


def transliterate(text: str, separator: str = "_") -> str:
    """
    Транслитерация русского текста в латиницу.
    separator: разделитель для пробелов ('_' для Avito, '-' для SuperJob)
    """
    if not text:
        return ""

    text_lower = text.lower().strip()

    # Проверяем особые случаи
    if text_lower in CITY_SPECIAL_CASES:
        result = CITY_SPECIAL_CASES[text_lower]
        # Меняем разделитель если нужно
        if separator == "-":
            return result.replace("_", "-")
        return result

    # Транслитерация
    result = []
    for char in text_lower:
        if char in TRANSLIT_MAP:
            result.append(TRANSLIT_MAP[char])
        elif char == ' ':
            result.append(separator)
        elif char == '-':
            result.append(separator)
        elif char.isalnum():
            result.append(char)
        # Остальные символы пропускаем

    return ''.join(result)


class BaseParser(ABC):
    """Базовый класс для парсеров вакансий"""

    name: str = "base"

    @abstractmethod
    async def search(self, filters: SearchFilters, limit: int = 20) -> list[Vacancy]:
        """Поиск вакансий по фильтрам"""
        pass

    def transliterate_city(self, city: str, separator: str = "_") -> str:
        """Транслитерация города для URL"""
        return transliterate(city, separator)

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

        # Проверка типа занятости (удаленка, полный день и т.д.)
        if filters.employment_type:
            if vacancy.employment_type != filters.employment_type:
                return False

        return True
