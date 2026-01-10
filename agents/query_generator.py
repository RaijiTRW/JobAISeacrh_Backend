"""
AI-генератор поисковых запросов
Генерирует разнообразные варианты запросов для максимального охвата
"""

import httpx
import json
from config import get_settings


class QueryGenerator:
    """AI-генератор поисковых запросов"""

    def __init__(self):
        self.settings = get_settings()

    async def generate_queries(self, user_request: str, context: str = "") -> list[str]:
        """
        Генерирует список поисковых запросов на основе запроса пользователя.
        Возвращает 5-10 разных вариантов для максимального охвата.
        """
        prompt = f"""Ты — генератор поисковых запросов для поиска вакансий в России.

Запрос пользователя: "{user_request}"
{f"Контекст: {context}" if context else ""}

Сгенерируй 6-10 РАЗНЫХ поисковых запросов для hh.ru, Avito и SuperJob.

ПРАВИЛА:
1. Используй синонимы и альтернативные названия
2. Добавляй варианты на русском И английском (где применимо)
3. Для маркетплейсов (ПВЗ, Wildberries, Ozon) — добавляй "пункт выдачи" к названию
4. НЕ добавляй "курьер", "сборщик", "логист", "водитель" если не просили
5. Для IT — добавляй конкретные технологии и фреймворки
6. Запросы должны быть короткими (2-4 слова)

ПРИМЕРЫ:
"пвз" → ["пункт выдачи заказов", "менеджер пвз", "оператор пвз", "сотрудник пвз", "пункт выдачи Wildberries", "пункт выдачи Ozon", "администратор пвз", "работа пвз"]
"python" → ["Python разработчик", "Python developer", "Django разработчик", "FastAPI", "backend Python", "Python junior", "Python middle", "программист Python"]
"бариста" → ["бариста", "barista", "кофейня работа", "бармен-бариста", "кофе бариста", "бариста кафе"]
"менеджер" → уточни какой именно! Не генерируй запросы для общего "менеджер"

Ответь ТОЛЬКО JSON массивом строк, например:
["запрос 1", "запрос 2", "запрос 3"]"""

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.settings.openrouter_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.validator_model_name,  # Используем быструю модель
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 500,
                        "temperature": 0.3,  # Немного креативности
                    },
                    timeout=30.0,
                )

                if response.status_code != 200:
                    print(f"[QueryGenerator] AI error: {response.status_code}")
                    return self._fallback_queries(user_request)

                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "[]")

                # Парсим JSON ответ
                try:
                    start = content.find("[")
                    end = content.rfind("]") + 1
                    if start >= 0 and end > start:
                        json_str = content[start:end]
                        queries = json.loads(json_str)
                        print(f"[QueryGenerator] Generated {len(queries)} queries: {queries}")
                        return queries[:10]  # Максимум 10
                except json.JSONDecodeError:
                    print(f"[QueryGenerator] Parse error: {content}")
                    return self._fallback_queries(user_request)

        except Exception as e:
            print(f"[QueryGenerator] Error: {e}")
            return self._fallback_queries(user_request)

        return self._fallback_queries(user_request)

    def _fallback_queries(self, user_request: str) -> list[str]:
        """Запасные запросы если AI не сработал"""
        # Простая логика на основе ключевых слов
        request_lower = user_request.lower()

        if any(kw in request_lower for kw in ["пвз", "пункт выдачи", "wildberries", "wb", "ozon", "озон"]):
            return [
                "пункт выдачи заказов",
                "менеджер пвз",
                "оператор пвз",
                "сотрудник пвз",
                "пункт выдачи Wildberries",
                "пункт выдачи Ozon",
            ]

        if any(kw in request_lower for kw in ["python", "питон", "пайтон"]):
            return [
                "Python разработчик",
                "Python developer",
                "Django разработчик",
                "FastAPI",
                "backend Python",
            ]

        if any(kw in request_lower for kw in ["frontend", "фронтенд", "react", "vue"]):
            return [
                "фронтенд разработчик",
                "frontend developer",
                "React разработчик",
                "Vue разработчик",
                "JavaScript developer",
            ]

        if any(kw in request_lower for kw in ["бариста", "barista", "кофе"]):
            return [
                "бариста",
                "barista",
                "кофейня",
                "бармен-бариста",
            ]

        # Дефолт — просто запрос пользователя
        return [user_request]


# Singleton
query_generator = QueryGenerator()
