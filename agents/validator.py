"""
Агент валидации вакансий
Фильтрует нерелевантные вакансии на основе предпочтений пользователя
"""

import httpx
from config import get_settings
from models.vacancy import Vacancy
from models.chat import UserPreferences


class VacancyValidator:
    """Агент для проверки релевантности вакансий"""

    # Контекстные стоп-слова: минимальный набор, только явный мусор
    # Для ПВЗ НЕ добавляем стоп-слова - пусть AI фильтрует
    CONTEXT_STOP_WORDS = {
        "бариста": ["официант", "повар", "посудомойщик"],
        "кассир": ["грузчик", "охранник"],
    }

    def __init__(self):
        self.settings = get_settings()

    async def validate_batch(
        self,
        vacancies: list[Vacancy],
        preferences: UserPreferences,
        queries: list[str] = None,
    ) -> list[Vacancy]:
        """
        Валидация списка вакансий
        Возвращает только релевантные
        """
        if not vacancies:
            return []

        print(f"[Validator] Input: {len(vacancies)} vacancies, queries={queries}")

        # Сначала убираем только явный мусор (курьеры, промоутеры)
        pre_filtered = self._quick_filter(vacancies, preferences, queries)
        print(f"[Validator] After quick filter: {len(pre_filtered)} vacancies")

        # Если много вакансий — AI фильтрует точнее
        if len(pre_filtered) > 15:
            filtered = await self._ai_filter(pre_filtered, preferences)
            # Fallback: если AI вернул 0, берём первые 15 из pre_filtered
            if len(filtered) == 0:
                print(f"[Validator] AI returned 0, using pre_filtered")
                return pre_filtered[:15]
            return filtered

        return pre_filtered

    def _quick_filter(
        self,
        vacancies: list[Vacancy],
        preferences: UserPreferences,
        queries: list[str] = None,
    ) -> list[Vacancy]:
        """Быстрая фильтрация без AI"""
        result = []

        # Стоп-слова (всегда фильтруем)
        stop_words = [
            "курьер", "доставщик", "разнорабочий",
            "промоутер", "раздача листовок", "расклейщик",
        ]

        # Добавляем контекстные стоп-слова на основе queries
        if queries:
            queries_lower = " ".join(queries).lower()
            for context_key, context_stops in self.CONTEXT_STOP_WORDS.items():
                if context_key in queries_lower:
                    stop_words.extend(context_stops)
                    print(f"[Validator] Added context stops for '{context_key}': {context_stops}")

        # Пользовательские исключения отдельно (проверяются по всему тексту)
        user_exclusions = [w.lower().strip() for w in preferences.exclude_keywords if w]

        # Убираем дубликаты из стоп-слов
        stop_words = list(set(stop_words))
        print(f"[Validator] Stop words (title only): {stop_words}")
        print(f"[Validator] User exclusions (full text): {user_exclusions}")

        for vacancy in vacancies:
            title_lower = (vacancy.title or "").lower()
            company_lower = (vacancy.company or "").lower()

            # Полный текст только для пользовательских исключений
            full_text = f"{title_lower} {company_lower} {(vacancy.description or '').lower()}"

            # Проверка стоп-слов ТОЛЬКО в названии (title)
            # Это важно! В описании ПВЗ может быть "приём от водителя" - это ок
            excluded = False
            for word in stop_words:
                if word and word in title_lower:
                    print(f"[Validator] Excluded '{vacancy.title}' - stop word '{word}' in title")
                    excluded = True
                    break

            if excluded:
                continue

            # Пользовательские исключения проверяем везде (осознанный выбор)
            user_excluded = False
            for word in user_exclusions:
                if word and word in full_text:
                    print(f"[Validator] Excluded '{vacancy.title}' - user exclusion '{word}'")
                    user_excluded = True
                    break

            if user_excluded:
                continue

            # Проверка зарплаты
            if preferences.salary_from:
                if vacancy.salary_to and vacancy.salary_to < preferences.salary_from:
                    continue

            result.append(vacancy)

        print(f"[Validator] Quick filter: {len(vacancies)} -> {len(result)} vacancies")
        return result

    async def _ai_filter(
        self,
        vacancies: list[Vacancy],
        preferences: UserPreferences,
    ) -> list[Vacancy]:
        """Фильтрация с помощью AI для сложных случаев"""

        # Формируем описание предпочтений
        pref_text = f"""
Пользователь ищет: {preferences.query or 'не указано'}
Город: {preferences.city or 'любой'}
Зарплата от: {preferences.salary_from or 'не указана'} ₽
Опыт: {preferences.experience or 'любой'}
Формат: {preferences.employment_type or 'любой'}
НЕ предлагать: {', '.join(preferences.exclude_keywords) if preferences.exclude_keywords else 'не указано'}
"""

        # Формируем список вакансий для проверки
        vacancy_list = []
        for i, v in enumerate(vacancies[:30]):  # Ограничиваем для экономии токенов
            vacancy_list.append(f"{i}. {v.title} | {v.company} | {v.salary_display} | {v.city}")

        prompt = f"""Ты — умный фильтр вакансий.

ЗАПРОС ПОЛЬЗОВАТЕЛЯ:
{pref_text}

ВАКАНСИИ:
{chr(10).join(vacancy_list)}

ПРАВИЛА ФИЛЬТРАЦИИ:

Для ПВЗ (пункт выдачи заказов):
✓ ВКЛЮЧАЙ: менеджер ПВЗ, сотрудник ПВЗ, оператор ПВЗ, администратор пункта выдачи, кассир ПВЗ
✗ ИСКЛЮЧАЙ: сборщик, комплектовщик, кладовщик, логист, грузчик, водитель, курьер — это СКЛАД!

Общие правила:
- Исключай MLM, сетевой маркетинг, пирамиды
- Исключай курьеров, промоутеров (если не просили)
- Если сомневаешься — ВКЛЮЧАЙ вакансию

Верни номера подходящих вакансий через запятую.
Ответ (только числа):"""

        try:
            headers = {
                "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://ai-working-search.ru",
                "X-Title": "AI Working Search",
            }

            payload = {
                "model": self.settings.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0,  # Стабильный результат
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Парсим номера
            valid_indices = []
            for part in text.replace(",", " ").split():
                try:
                    idx = int(part.strip())
                    if 0 <= idx < len(vacancies):
                        valid_indices.append(idx)
                except ValueError:
                    continue

            return [vacancies[i] for i in valid_indices]

        except Exception as e:
            print(f"AI filter error: {e}")
            return vacancies[:20]  # Fallback


# Singleton
validator = VacancyValidator()
