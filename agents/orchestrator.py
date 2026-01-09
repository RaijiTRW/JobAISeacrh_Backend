"""
Главный агент-оркестратор
Управляет потоком: вопросы → поиск → валидация → ответ
Использует OpenRouter API
"""

import json
import httpx
from config import get_settings
from models.chat import ChatRequest, ChatResponse, UserPreferences
from models.vacancy import SearchFilters
from tools.search import vacancy_search
from agents.validator import VacancyValidator


SYSTEM_PROMPT = """Ты — AI-помощник для поиска работы в России. Твоя задача — помочь пользователю найти подходящие вакансии.

ПРАВИЛА:
1. Если пользователь не указал город или сферу работы — обязательно уточни
2. Задавай вопросы по одному, не перегружай пользователя
3. Когда есть достаточно информации — используй инструмент search_vacancies
4. Отвечай кратко и по делу, без воды
5. Используй разговорный русский язык

УТОЧНЯЮЩИЕ ВОПРОСЫ (задавай если не указано):
- Город/регион для поиска
- Сфера деятельности / должность
- Желаемая зарплата (от)
- Опыт работы
- Формат работы (офис/удалёнка/гибрид)
- Что НЕ предлагать (курьеры, продажи и т.д.)

ФОРМАТ ОТВЕТА С ВАКАНСИЯМИ:
Когда нашёл вакансии, кратко опиши что нашёл и предложи посмотреть карточки.
Не перечисляй вакансии текстом — они будут показаны карточками.

Пример: "Нашёл 15 вакансий менеджера в Москве от 80 000 ₽. Отфильтровал курьеров и холодные продажи. Смотри карточки справа 👉"
"""


class OpenRouterClient:
    """Клиент для OpenRouter API"""

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url

    async def chat_completion(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> dict:
        """Вызов chat completion через OpenRouter"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ai-working-search.ru",
            "X-Title": "AI Working Search",
        }

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=60.0,
            )
            if response.status_code != 200:
                print(f"OpenRouter error [{response.status_code}]: {response.text}")
            response.raise_for_status()
            return response.json()


class Orchestrator:
    def __init__(self):
        self.settings = get_settings()
        self.client = OpenRouterClient(
            api_key=self.settings.openrouter_api_key,
            base_url=self.settings.openrouter_base_url,
        )
        self.validator = VacancyValidator()

    async def process_message(
        self,
        request: ChatRequest,
        history: list[dict],
        preferences: UserPreferences,
    ) -> ChatResponse:
        """Обработка сообщения пользователя"""
        context = self._build_context(preferences)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # История
        for msg in history:
            messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        # Новое сообщение
        messages.append({
            "role": "user",
            "content": f"{context}\n\nСообщение пользователя: {request.message}",
        })

        # Инструменты в формате OpenAI
        tools = [self._convert_tool_to_openai(vacancy_search.get_tool_definition())]

        # Вызов через OpenRouter
        response = await self.client.chat_completion(
            model=self.settings.model_name,
            messages=messages,
            tools=tools,
            max_tokens=self.settings.max_tokens,
        )

        # Обработка ответа
        return await self._process_response(response, preferences)

    def _convert_tool_to_openai(self, anthropic_tool: dict) -> dict:
        """Конвертация формата инструмента Anthropic в OpenAI"""
        return {
            "type": "function",
            "function": {
                "name": anthropic_tool["name"],
                "description": anthropic_tool["description"],
                "parameters": anthropic_tool["input_schema"],
            }
        }

    async def _process_response(
        self,
        response: dict,
        preferences: UserPreferences,
    ) -> ChatResponse:
        """Обработка ответа от OpenRouter"""
        text_parts = []
        vacancies = []

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})

        # Текстовый ответ
        if message.get("content"):
            text_parts.append(message["content"])

        # Tool calls
        tool_calls = message.get("tool_calls", [])
        print(f"Tool calls received: {len(tool_calls)}")
        for tool_call in tool_calls:
            print(f"Tool call: {tool_call.get('function', {}).get('name')}")
            if tool_call.get("function", {}).get("name") == "search_vacancies":
                try:
                    args = json.loads(tool_call["function"]["arguments"])
                    print(f"Search args: {args}")
                    filters = SearchFilters(**args)
                    result = await vacancy_search.search(filters)
                    print(f"Search result: {len(result.vacancies)} vacancies found")

                    # Валидируем вакансии
                    validated = await self.validator.validate_batch(
                        result.vacancies,
                        preferences,
                    )
                    print(f"Validated: {len(validated)} vacancies")

                    vacancies = [v.model_dump(mode='json') for v in validated]

                    text_parts.append(
                        f"\n\nНашёл {len(validated)} подходящих вакансий из {result.total_found} найденных."
                    )
                except Exception as e:
                    print(f"Error processing tool call: {e}")
                    import traceback
                    traceback.print_exc()

        return ChatResponse(
            message=" ".join(text_parts) if text_parts else "Что-то пошло не так, попробуй ещё раз.",
            vacancies=vacancies,
            needs_clarification=len(vacancies) == 0 and not preferences.is_complete(),
            chat_id=str(hash(str(preferences))),
        )

    def _build_context(self, preferences: UserPreferences) -> str:
        """Формирование контекста из предпочтений"""
        parts = ["Известно о пользователе:"]

        if preferences.query:
            parts.append(f"- Ищет: {preferences.query}")
        if preferences.city:
            parts.append(f"- Город: {preferences.city}")
        if preferences.salary_from:
            parts.append(f"- Зарплата от: {preferences.salary_from} ₽")
        if preferences.experience:
            parts.append(f"- Опыт: {preferences.experience}")
        if preferences.employment_type:
            parts.append(f"- Формат: {preferences.employment_type}")
        if preferences.exclude_keywords:
            parts.append(f"- Не предлагать: {', '.join(preferences.exclude_keywords)}")

        if len(parts) == 1:
            return "Пока ничего не известно о предпочтениях пользователя."

        return "\n".join(parts)

    async def extract_preferences(self, message: str, current: UserPreferences) -> UserPreferences:
        """Извлечение предпочтений из сообщения пользователя"""
        current_prefs = []
        if current.query:
            current_prefs.append(f"- query: {current.query}")
        if current.city:
            current_prefs.append(f"- city: {current.city}")
        if current.salary_from:
            current_prefs.append(f"- salary_from: {current.salary_from}")
        if current.experience:
            current_prefs.append(f"- experience: {current.experience}")
        if current.employment_type:
            current_prefs.append(f"- employment_type: {current.employment_type}")
        if current.exclude_keywords:
            current_prefs.append(f"- exclude_keywords: {current.exclude_keywords}")

        current_prefs_str = "\n".join(current_prefs) if current_prefs else "Пока ничего не известно"

        extraction_prompt = f"""Извлеки НОВУЮ информацию о поиске работы из сообщения пользователя.
НЕ повторяй уже известные данные, только добавляй новое.

Уже известно:
{current_prefs_str}

Верни ТОЛЬКО JSON без пояснений, с полями (только те что НОВЫЕ в сообщении):
- query: должность/сфера
- city: город
- salary_from: минимальная зарплата (число)
- experience: опыт (no_experience, 1-3, 3-6, 6+)
- employment_type: формат (full, part, remote)
- exclude_keywords: что не предлагать (массив строк)

Новое сообщение: {message}

JSON (только новые данные):"""

        try:
            response = await self.client.chat_completion(
                model=self.settings.model_name,
                messages=[{"role": "user", "content": extraction_prompt}],
                max_tokens=500,
            )

            text = response.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Находим JSON в ответе
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])

                if data.get("query"):
                    current.query = data["query"]
                if data.get("city"):
                    current.city = data["city"]
                if data.get("salary_from"):
                    current.salary_from = int(data["salary_from"])
                if data.get("experience"):
                    current.experience = data["experience"]
                if data.get("employment_type"):
                    current.employment_type = data["employment_type"]
                if data.get("exclude_keywords"):
                    current.exclude_keywords.extend(data["exclude_keywords"])

        except Exception as e:
            print(f"Error extracting preferences: {e}")

        print(f"Current preferences: query={current.query}, city={current.city}, salary={current.salary_from}, exp={current.experience}")
        return current


# Singleton
orchestrator = Orchestrator()
