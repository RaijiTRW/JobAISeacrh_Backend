"""
Валидатор вакансий
AI-фильтрация с пониманием контекста
"""

import httpx
from models.vacancy import Vacancy
from models.chat import UserPreferences
from config import get_settings


class VacancyValidator:
    """AI-валидатор с пониманием контекста"""

    # Стоп-слова в названии - всегда исключаем (без AI)
    TITLE_STOP_WORDS = [
        "курьер", "доставщик", "водитель", "грузчик",
        "промоутер", "листовки", "расклейщик",
        "сборщик", "комплектовщик", "кладовщик", "упаковщик",
        "логист", "оператор склада", "работник склада",
    ]

    def __init__(self):
        self.settings = get_settings()

    async def validate_batch(
        self,
        vacancies: list[Vacancy],
        preferences: UserPreferences,
        queries: list[str] = None,
    ) -> list[Vacancy]:
        """AI-фильтрация вакансий"""
        if not vacancies:
            return []

        queries = queries or []
        queries_text = " ".join(queries).lower()

        print(f"[Validator] Input: {len(vacancies)} vacancies")
        print(f"[Validator] Queries: {queries}")

        # Пользовательские исключения
        user_exclusions = [w.lower().strip() for w in preferences.exclude_keywords if w]

        # Шаг 1: Быстрая фильтрация стоп-словами (без AI)
        pre_filtered = []
        for vacancy in vacancies:
            title_lower = (vacancy.title or "").lower()

            # Проверяем стоп-слова
            has_stop = False
            for stop in self.TITLE_STOP_WORDS:
                if stop in title_lower:
                    print(f"[Validator] STOP '{vacancy.title}' - contains '{stop}'")
                    has_stop = True
                    break
            if has_stop:
                continue

            # Пользовательские исключения
            full_text = f"{title_lower} {(vacancy.description or '').lower()} {(vacancy.company or '').lower()}"
            user_excluded = False
            for excl in user_exclusions:
                if excl in full_text:
                    print(f"[Validator] EXCLUDED '{vacancy.title}' - user exclusion '{excl}'")
                    user_excluded = True
                    break
            if user_excluded:
                continue

            # Проверка зарплаты
            if preferences.salary_from:
                if vacancy.salary_to and vacancy.salary_to < preferences.salary_from:
                    continue

            pre_filtered.append(vacancy)

        print(f"[Validator] After pre-filter: {len(pre_filtered)} vacancies")

        if not pre_filtered:
            return []

        # Шаг 2: AI-фильтрация (батчами по 10)
        result = await self._ai_filter(pre_filtered, queries_text)

        print(f"[Validator] Result: {len(vacancies)} -> {len(result)} vacancies")
        return result

    async def _ai_filter(self, vacancies: list[Vacancy], search_query: str) -> list[Vacancy]:
        """AI-фильтрация вакансий"""
        result = []
        batch_size = 15  # Обрабатываем по 15 вакансий за раз

        for i in range(0, len(vacancies), batch_size):
            batch = vacancies[i:i + batch_size]
            approved_ids = await self._check_batch_with_ai(batch, search_query)

            for vacancy in batch:
                if vacancy.id in approved_ids:
                    print(f"[Validator] AI OK '{vacancy.title}'")
                    result.append(vacancy)
                else:
                    print(f"[Validator] AI SKIP '{vacancy.title}'")

        return result

    async def _check_batch_with_ai(self, vacancies: list[Vacancy], search_query: str) -> set[str]:
        """Проверяет батч вакансий через AI, возвращает ID подходящих"""

        # Формируем список вакансий для проверки
        vacancy_list = []
        for i, v in enumerate(vacancies):
            vacancy_list.append(f"{i+1}. [{v.id}] {v.title} | {v.company} | {v.description[:200] if v.description else 'нет описания'}")

        prompt = f"""Ты — фильтр вакансий. Пользователь ищет: "{search_query}"

Проверь каждую вакансию и ответь ТОЛЬКО списком ID подходящих вакансий.

ПРАВИЛА:
1. Вакансия подходит если она РЕАЛЬНО связана с запросом пользователя
2. Для "пвз/пункт выдачи" подходят: менеджер пвз, сотрудник пункта выдачи, оператор пвз, работа в Wildberries/Ozon/маркетплейсах (НЕ курьер, НЕ сборщик, НЕ логист)
3. Общие названия типа "Администратор", "Менеджер" - подходят ТОЛЬКО если в описании/компании явно указан контекст пвз/маркетплейс
4. НЕ подходят: курьеры, сборщики, логисты, водители, складские работники

Вакансии:
{chr(10).join(vacancy_list)}

Ответь ТОЛЬКО в формате JSON массива ID подходящих вакансий, например:
["avito_123", "hh_456", "sj_789"]

Если НИ ОДНА не подходит — ответь: []"""

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.settings.openrouter_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.validator_model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 500,
                        "temperature": 0,
                    },
                    timeout=30.0,
                )

                if response.status_code != 200:
                    print(f"[Validator] AI error: {response.status_code}")
                    # При ошибке пропускаем все (лучше показать больше чем потерять)
                    return {v.id for v in vacancies}

                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "[]")

                # Парсим JSON ответ
                import json
                try:
                    # Ищем JSON в ответе
                    start = content.find("[")
                    end = content.rfind("]") + 1
                    if start >= 0 and end > start:
                        json_str = content[start:end]
                        approved_ids = json.loads(json_str)
                        return set(approved_ids)
                except json.JSONDecodeError:
                    print(f"[Validator] AI response parse error: {content}")
                    return {v.id for v in vacancies}

        except Exception as e:
            print(f"[Validator] AI error: {e}")
            # При ошибке пропускаем все
            return {v.id for v in vacancies}

        return set()

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
