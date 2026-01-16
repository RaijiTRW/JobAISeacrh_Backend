"""
Простой экстрактор параметров из запроса пользователя
Извлекает: город, профессию, зарплату, опыт
"""

import httpx
import json
import re
from typing import Optional
from config import get_settings
from models.chat import UserPreferences
from services.user_profile import UserData


class ParamExtractor:
    """Простой экстрактор параметров поиска"""

    def __init__(self):
        self.settings = get_settings()

    async def extract(
        self,
        message: str,
        current_prefs: UserPreferences,
        user_data: Optional[UserData] = None,
    ) -> tuple[UserPreferences, bool, Optional[str]]:
        """
        Извлекает параметры из сообщения

        Returns:
            (preferences, needs_clarification, clarification_message)
        """
        # Извлекаем новые параметры
        new_prefs = await self._extract_params(message, current_prefs)

        # Дополняем из профиля если нужно
        new_prefs = self._merge_with_profile(new_prefs, user_data)

        # Проверяем достаточно ли данных для поиска
        needs_clarification, clarification_msg = self._check_completeness(new_prefs)

        return new_prefs, needs_clarification, clarification_msg

    async def _extract_params(
        self, message: str, current: UserPreferences
    ) -> UserPreferences:
        """Извлечение параметров через LLM"""

        current_info = []
        if current.query:
            current_info.append(f"- Профессия: {current.query}")
        if current.city:
            current_info.append(f"- Город: {current.city}")
        if current.salary_from:
            current_info.append(f"- Зарплата от: {current.salary_from}")
        if current.experience:
            current_info.append(f"- Опыт: {current.experience}")
        if current.employment_type:
            current_info.append(f"- Формат: {current.employment_type}")
        if current.exclude_keywords:
            current_info.append(f"- Исключить: {', '.join(current.exclude_keywords)}")

        current_str = "\n".join(current_info) if current_info else "Пока ничего не известно"

        prompt = f"""Извлеки информацию о поиске работы из сообщения.
НЕ повторяй уже известное, только НОВОЕ из сообщения.

Уже известно:
{current_str}

Новое сообщение: {message}

Верни ТОЛЬКО JSON без пояснений:
{{
  "query": "название профессии/должности (если есть в сообщении)",
  "city": "город (если есть в сообщении)",
  "salary_from": число минимальной зарплаты (если есть),
  "experience": "no_experience|1-3|3-6|6+" (если есть),
  "employment_type": "full|part|remote" (если есть),
  "exclude_keywords": ["слова для исключения"] (если есть)
}}

ВАЖНО:
- Оставляй поля пустыми если их нет в сообщении
- Зарплату извлекай как число: "от 100к" → 100000, "150-200" → 150000
- exclude_keywords: если "убрать/без/не показывай X" → добавь X

JSON:"""

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.settings.openrouter_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 500,
                        "temperature": 0.1,
                    },
                    timeout=30.0,
                )

                if response.status_code != 200:
                    print(f"[ParamExtractor] Error {response.status_code}")
                    return current

                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # Парсим JSON
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    params = json.loads(content[start:end])

                    # Обновляем только новые поля
                    if params.get("query"):
                        current.query = params["query"]
                    if params.get("city"):
                        current.city = params["city"]
                    if params.get("salary_from"):
                        current.salary_from = int(params["salary_from"])
                    if params.get("experience"):
                        current.experience = params["experience"]
                    if params.get("employment_type"):
                        current.employment_type = params["employment_type"]
                    if params.get("exclude_keywords"):
                        current.exclude_keywords.extend(params["exclude_keywords"])

        except Exception as e:
            print(f"[ParamExtractor] Error: {e}")

        return current

    def _merge_with_profile(
        self, prefs: UserPreferences, user_data: Optional[UserData]
    ) -> UserPreferences:
        """Дополняет параметры из профиля пользователя"""
        if not user_data:
            return prefs

        # Город из профиля
        if not prefs.city and user_data.profile and user_data.profile.city:
            prefs.city = user_data.profile.city

        # Профессия из резюме
        if not prefs.query and user_data.resume and user_data.resume.desired_position:
            prefs.query = user_data.resume.desired_position

        # Зарплата из резюме
        if not prefs.salary_from and user_data.resume and user_data.resume.desired_salary:
            try:
                salary_str = user_data.resume.desired_salary
                numbers = re.findall(r'\d+', salary_str.replace(' ', ''))
                if numbers:
                    prefs.salary_from = int(numbers[0])
            except (ValueError, AttributeError):
                pass

        return prefs

    def _check_completeness(
        self, prefs: UserPreferences
    ) -> tuple[bool, Optional[str]]:
        """Проверяет достаточно ли данных для поиска"""

        # Обязательно нужны: профессия и город
        if not prefs.query:
            return True, "Какую работу ищешь? (профессия, сфера)"

        if not prefs.city:
            return True, "В каком городе искать?"

        # Зарплата опциональна, можем искать без неё
        # Но спросим один раз
        if not prefs.salary_from and not prefs.asked_salary:
            prefs.asked_salary = True
            return True, "От какой зарплаты искать? (можно пропустить если не важно)"

        # Всё готово для поиска
        return False, None

    async def generate_queries(self, base_query: str) -> list[str]:
        """Генерирует варианты поисковых запросов"""

        prompt = f"""Сгенерируй 3-6 вариантов поисковых запросов для: "{base_query}"

Правила:
- Основной запрос как есть
- Синонимы и альтернативные названия
- Варианты написания (русское/английское)
- Если "ПВЗ" → добавь "пункт выдачи", "выдача заказов", "пункт выдачи Wildberries"
- Если "программист" → добавь конкретные языки (Python, Java, JavaScript)

Верни только JSON массив строк без пояснений:
["запрос 1", "запрос 2", ...]"""

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.settings.openrouter_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 300,
                        "temperature": 0.3,
                    },
                    timeout=30.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]

                    # Парсим JSON массив
                    start = content.find("[")
                    end = content.rfind("]") + 1
                    if start >= 0 and end > start:
                        queries = json.loads(content[start:end])
                        return queries[:6]  # Максимум 6
        except Exception as e:
            print(f"[ParamExtractor] Query generation error: {e}")

        # Fallback - возвращаем оригинальный запрос
        return [base_query]


# Singleton
param_extractor = ParamExtractor()
