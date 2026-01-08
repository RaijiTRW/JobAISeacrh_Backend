"""
Агент валидации вакансий
Фильтрует нерелевантные вакансии на основе предпочтений пользователя
"""

import httpx
from app.config import get_settings
from app.models.vacancy import Vacancy
from app.models.chat import UserPreferences


class VacancyValidator:
    """Агент для проверки релевантности вакансий"""

    def __init__(self):
        self.settings = get_settings()

    async def validate_batch(
        self,
        vacancies: list[Vacancy],
        preferences: UserPreferences,
    ) -> list[Vacancy]:
        """
        Валидация списка вакансий
        Возвращает только релевантные
        """
        if not vacancies:
            return []

        # Сначала быстрая фильтрация по ключевым словам
        filtered = self._quick_filter(vacancies, preferences)

        # Если осталось много — используем AI для точной фильтрации
        if len(filtered) > 20:
            filtered = await self._ai_filter(filtered, preferences)

        return filtered

    def _quick_filter(
        self,
        vacancies: list[Vacancy],
        preferences: UserPreferences,
    ) -> list[Vacancy]:
        """Быстрая фильтрация без AI"""
        result = []

        # Стоп-слова (всегда фильтруем)
        stop_words = [
            "курьер", "доставщик", "грузчик", "разнорабочий",
            "промоутер", "раздача листовок", "расклейщик",
        ]

        # Добавляем пользовательские исключения
        stop_words.extend([w.lower() for w in preferences.exclude_keywords])

        for vacancy in vacancies:
            text = f"{vacancy.title} {vacancy.description}".lower()

            # Проверка стоп-слов
            if any(word in text for word in stop_words):
                continue

            # Проверка зарплаты
            if preferences.salary_from:
                if vacancy.salary_to and vacancy.salary_to < preferences.salary_from:
                    continue

            result.append(vacancy)

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

        prompt = f"""Ты — фильтр вакансий. Проверь каждую вакансию на соответствие запросу.

ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ:
{pref_text}

ВАКАНСИИ:
{chr(10).join(vacancy_list)}

ЗАДАЧА:
Верни ТОЛЬКО номера вакансий, которые ПОДХОДЯТ пользователю.
Отфильтруй:
- Явно нерелевантные должности
- Вакансии с подозрительно низкой зарплатой для должности
- Скрытые "холодные продажи", MLM, сетевой маркетинг
- Курьеров, промоутеров если это не запрашивалось

Ответ — только числа через запятую. Пример: 0, 2, 5, 7"""

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
