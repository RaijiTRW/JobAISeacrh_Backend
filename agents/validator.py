"""
Валидатор вакансий
Жёсткая фильтрация по ключевым словам
"""

from models.vacancy import Vacancy
from models.chat import UserPreferences


class VacancyValidator:
    """Валидатор с позитивной фильтрацией"""

    # Ключевые слова для каждой категории - проверяем в названии И описании
    MUST_CONTAIN = {
        "пвз": [
            # Основные
            "пвз", "pvz",
            # Падежи "пункт выдачи"
            "пункт выдачи", "пункта выдачи", "пункте выдачи", "пунктом выдачи", "пункту выдачи",
            # Падежи "выдача заказов"
            "выдача заказов", "выдачи заказов", "выдачу заказов", "выдачей заказов",
            # Маркетплейсы (если в контексте работы, не курьер)
            "wildberries", "вайлдберриз", "вайлдбериз", "вб",
            "ozon", "озон",
            "яндекс маркет", "yandex market",
        ],
        "бариста": ["бариста", "barista", "кофейня", "кофе-бар"],
        "кассир": ["кассир", "касса", "кассов"],
        "продавец": ["продавец", "продавц", "консультант"],
    }

    # Стоп-слова в названии - всегда исключаем
    TITLE_STOP_WORDS = [
        "курьер", "доставщик", "водитель", "грузчик",
        "промоутер", "листовки", "расклейщик",
        "сборщик", "комплектовщик", "кладовщик", "упаковщик",
        "логист", "оператор склада", "работник склада",
    ]

    def __init__(self):
        pass

    async def validate_batch(
        self,
        vacancies: list[Vacancy],
        preferences: UserPreferences,
        queries: list[str] = None,
    ) -> list[Vacancy]:
        """Фильтрация вакансий"""
        if not vacancies:
            return []

        queries = queries or []
        queries_text = " ".join(queries).lower()

        print(f"[Validator] Input: {len(vacancies)} vacancies")
        print(f"[Validator] Queries: {queries}")

        # Определяем категорию запроса
        category = self._detect_category(queries_text)
        print(f"[Validator] Category: {category}")

        # Получаем обязательные ключевые слова для категории
        must_contain = self.MUST_CONTAIN.get(category, [])

        # Пользовательские исключения
        user_exclusions = [w.lower().strip() for w in preferences.exclude_keywords if w]

        result = []
        for vacancy in vacancies:
            title_lower = (vacancy.title or "").lower()
            company_lower = (vacancy.company or "").lower()
            description_lower = (vacancy.description or "").lower()
            url_lower = (vacancy.url or "").lower()
            # Полный текст для поиска ключевых слов (включая URL — там часто есть ключи)
            full_text = f"{title_lower} {description_lower} {company_lower} {url_lower}"

            # 1. Проверяем стоп-слова в названии (только в названии!)
            has_stop = False
            for stop in self.TITLE_STOP_WORDS:
                if stop in title_lower:
                    print(f"[Validator] STOP '{vacancy.title}' - contains '{stop}'")
                    has_stop = True
                    break
            if has_stop:
                continue

            # 2. Если есть категория - ищем ключевые слова в названии, описании ИЛИ URL
            if must_contain:
                has_keyword = False
                found_in = None
                for keyword in must_contain:
                    if keyword in title_lower:
                        has_keyword = True
                        found_in = "title"
                        break
                    elif keyword in description_lower:
                        has_keyword = True
                        found_in = "desc"
                        break
                    elif keyword in url_lower:
                        has_keyword = True
                        found_in = "url"
                        break
                if not has_keyword:
                    print(f"[Validator] SKIP '{vacancy.title}' - no keywords for {category}")
                    continue

            # 3. Пользовательские исключения
            user_excluded = False
            for excl in user_exclusions:
                if excl in full_text or excl in (vacancy.description or "").lower():
                    print(f"[Validator] EXCLUDED '{vacancy.title}' - user exclusion '{excl}'")
                    user_excluded = True
                    break
            if user_excluded:
                continue

            # 4. Проверка зарплаты
            if preferences.salary_from:
                if vacancy.salary_to and vacancy.salary_to < preferences.salary_from:
                    continue

            result.append(vacancy)
            print(f"[Validator] OK '{vacancy.title}'")

        print(f"[Validator] Result: {len(vacancies)} -> {len(result)} vacancies")
        return result

    def _detect_category(self, text: str) -> str:
        """Определяет категорию запроса"""
        if any(kw in text for kw in ["пвз", "пункт выдачи", "wildberries", "wb", "ozon", "озон", "вайлдберриз"]):
            return "пвз"
        if any(kw in text for kw in ["бариста", "barista", "кофе"]):
            return "бариста"
        if any(kw in text for kw in ["кассир", "касса"]):
            return "кассир"
        if any(kw in text for kw in ["продавец", "продажи", "консультант"]):
            return "продавец"
        return ""


# Singleton
validator = VacancyValidator()
