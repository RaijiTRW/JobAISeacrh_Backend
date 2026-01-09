"""
Главный агент-оркестратор
Управляет потоком: вопросы → поиск → валидация → ответ
Использует OpenRouter API
"""

import json
import re
import httpx
from typing import Optional
from config import get_settings
from models.chat import ChatRequest, ChatResponse, UserPreferences
from models.vacancy import SearchFilters
from tools.search import vacancy_search
from agents.validator import VacancyValidator
from services.user_profile import UserData


SYSTEM_PROMPT = """Ты — AI-помощник для поиска работы в России. Твоя задача — помочь пользователю найти подходящие вакансии.

ТВОЯ ГЛАВНАЯ ЗАДАЧА — ПРЕОБРАЗОВАТЬ ЗАПРОС ПОЛЬЗОВАТЕЛЯ В ПРАВИЛЬНЫЙ ПОИСКОВЫЙ ЗАПРОС:

ВАЖНО: Мы ищем на РОССИЙСКИХ сайтах (hh.ru, Avito, SuperJob). Запросы должны быть НА РУССКОМ!
Названия языков (Python, Java, React) можно оставлять как есть — они универсальны.

Примеры преобразования:
- "ИИ" или "AI" → query: "машинное обучение OR data scientist OR нейросети"
- "фронт" или "фронтенд" → query: "фронтенд разработчик OR React OR Vue"
- "бэк" или "бэкенд" → query: "бэкенд разработчик OR Python разработчик"
- "питон" → query: "Python разработчик OR Django"
- "джава" → query: "Java разработчик OR Spring"
- "программист" → уточни: какой язык? (Python, Java, JavaScript, C++ и т.д.)
- "менеджер" → уточни: какой? (продукт-менеджер, проджект-менеджер, менеджер по продажам)
- "аналитик" → уточни: какой? (аналитик данных, бизнес-аналитик, системный аналитик)
- "в IT" → уточни: какая роль? (разработка, тестирование, аналитика, DevOps, дизайн)

КОГДА ЗАДАВАТЬ ВОПРОСЫ:
1. Если запрос слишком общий ("хочу работу", "ищу работу") — спроси сферу
2. Если профессия размытая ("программист", "менеджер") — уточни специализацию
3. Если не указан город и его нет в профиле — спроси город
4. Задавай по ОДНОМУ вопросу за раз, не перегружай

КОГДА СРАЗУ ИСКАТЬ:
1. Если понятно что искать (конкретная профессия) И известен город — сразу ищи
2. Если в профиле есть город, а пользователь назвал профессию — сразу ищи
3. Если в резюме есть желаемая должность и город — можно предложить поискать

КАК ФОРМИРОВАТЬ ПАРАМЕТРЫ search_vacancies:
- query: преобразуй запрос пользователя в понятный поисковый запрос (см. примеры выше)
- city: из сообщения или из профиля пользователя
- salary_from: если пользователь указал или есть в "ТЕКУЩИЙ ПОИСК"
- experience: если пользователь указал
- employment_type: если пользователь указал (удалёнка → "remote", офис → "full")
- exclude_keywords: если пользователь сказал что НЕ хочет (курьеры, продажи и т.д.)

ПРАВИЛА ДИАЛОГА:
1. Отвечай кратко и по делу
2. Используй разговорный русский
3. Если знаешь имя — обращайся по имени
4. Не задавай все вопросы сразу — по одному

ПРИМЕРЫ:

Пользователь: "Хочу в ИИ"
Ты: "В какой роли? Разработка ML-моделей, аналитик данных, или что-то другое?"

Пользователь: "разработка моделей в Москве"
→ search_vacancies(query="машинное обучение OR data scientist OR ML разработчик", city="Москва")

Пользователь: "Ищу работу программистом" + в профиле город Казань
Ты: "На каком языке? Python, JavaScript, Java?"

Пользователь: "питон"
→ search_vacancies(query="Python разработчик OR Django", city="Казань")

Пользователь: "Фронтенд удалённо"
Ты: "В каком городе?"

Пользователь: "СПб"
→ search_vacancies(query="фронтенд разработчик OR React OR Vue", city="Санкт-Петербург", employment_type="remote")

Пользователь: "джава разработчик спб"
→ search_vacancies(query="Java разработчик OR Spring", city="Санкт-Петербург")

ФОРМАТ ОТВЕТА С ВАКАНСИЯМИ:
Кратко опиши что нашёл. Вакансии покажутся карточками автоматически.
Пример: "Нашёл 12 вакансий ML-инженера в Москве. Смотри карточки 👉"
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
        user_data: Optional[UserData] = None,
    ) -> ChatResponse:
        """Обработка сообщения пользователя"""
        context = self._build_context(preferences, user_data)

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
        return await self._process_response(response, preferences, user_data)

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
        user_data: Optional[UserData] = None,
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
                    print(f"Search args from AI: {args}")

                    # Дополняем параметры из preferences и user_data если ИИ их не указал
                    args = self._merge_with_preferences(args, preferences, user_data)
                    print(f"Search args after merge: {args}")

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

    def _merge_with_preferences(
        self,
        args: dict,
        preferences: UserPreferences,
        user_data: Optional[UserData] = None,
    ) -> dict:
        """Дополняет параметры поиска из preferences и user_data если ИИ их не указал"""

        # query — приоритет: args > preferences > резюме (желаемая должность)
        if not args.get("query"):
            if preferences.query:
                args["query"] = preferences.query
            elif user_data and user_data.resume and user_data.resume.desired_position:
                args["query"] = user_data.resume.desired_position

        # city — приоритет: args > preferences > профиль (город)
        if not args.get("city"):
            if preferences.city:
                args["city"] = preferences.city
            elif user_data and user_data.profile and user_data.profile.city:
                args["city"] = user_data.profile.city

        # salary_from — приоритет: args > preferences > резюме (желаемая зарплата)
        if not args.get("salary_from"):
            if preferences.salary_from:
                args["salary_from"] = preferences.salary_from
            elif user_data and user_data.resume and user_data.resume.desired_salary:
                # Пробуем извлечь число из строки зарплаты
                try:
                    salary_str = user_data.resume.desired_salary
                    # Убираем пробелы и нечисловые символы, оставляем первое число
                    numbers = re.findall(r'\d+', salary_str.replace(' ', ''))
                    if numbers:
                        args["salary_from"] = int(numbers[0])
                except (ValueError, AttributeError):
                    pass

        # experience — берём из preferences если ИИ не указал
        if not args.get("experience") and preferences.experience:
            args["experience"] = preferences.experience

        # employment_type — берём из preferences если ИИ не указал
        if not args.get("employment_type") and preferences.employment_type:
            args["employment_type"] = preferences.employment_type

        # exclude_keywords — ОБЪЕДИНЯЕМ (важно не потерять)
        ai_exclude = args.get("exclude_keywords", [])
        pref_exclude = preferences.exclude_keywords or []
        # Объединяем и убираем дубликаты
        combined_exclude = list(set(ai_exclude + pref_exclude))
        if combined_exclude:
            args["exclude_keywords"] = combined_exclude

        return args

    def _build_context(self, preferences: UserPreferences, user_data: Optional[UserData] = None) -> str:
        """Формирование контекста из предпочтений и данных пользователя"""
        parts = []

        # Данные из профиля
        if user_data and user_data.profile:
            profile = user_data.profile
            profile_parts = ["ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:"]

            if profile.first_name:
                name_parts = []
                if profile.first_name:
                    name_parts.append(profile.first_name)
                if profile.last_name:
                    name_parts.append(profile.last_name)
                if profile.patronymic:
                    name_parts.append(profile.patronymic)
                profile_parts.append(f"- Имя: {' '.join(name_parts)}")

            if profile.city:
                profile_parts.append(f"- Город проживания: {profile.city}")
            if profile.phone:
                profile_parts.append(f"- Телефон: {profile.phone}")

            if len(profile_parts) > 1:
                parts.append("\n".join(profile_parts))

        # Данные из резюме
        if user_data and user_data.resume:
            resume = user_data.resume
            resume_parts = ["РЕЗЮМЕ ПОЛЬЗОВАТЕЛЯ:"]

            if resume.desired_position:
                resume_parts.append(f"- Желаемая должность: {resume.desired_position}")
            if resume.desired_salary:
                resume_parts.append(f"- Желаемая зарплата: {resume.desired_salary}")
            if resume.skills:
                resume_parts.append(f"- Навыки: {resume.skills}")
            if resume.about:
                resume_parts.append(f"- О себе: {resume.about}")

            # Опыт работы
            if resume.work_experience:
                exp_parts = ["- Опыт работы:"]
                for exp in resume.work_experience[:3]:  # Максимум 3 места
                    exp_str = f"  • {exp.position or 'Должность не указана'}"
                    if exp.company:
                        exp_str += f" в {exp.company}"
                    if exp.start_date:
                        exp_str += f" ({exp.start_date}"
                        if exp.is_current:
                            exp_str += " - настоящее время)"
                        elif exp.end_date:
                            exp_str += f" - {exp.end_date})"
                        else:
                            exp_str += ")"
                    exp_parts.append(exp_str)
                resume_parts.extend(exp_parts)

            # Образование
            if resume.education:
                edu_parts = ["- Образование:"]
                for edu in resume.education[:2]:  # Максимум 2 записи
                    edu_str = f"  • {edu.degree or ''} {edu.field or ''}".strip()
                    if edu.institution:
                        edu_str += f", {edu.institution}"
                    if edu.end_year:
                        edu_str += f" ({edu.end_year})"
                    if edu_str.strip():
                        edu_parts.append(edu_str)
                if len(edu_parts) > 1:
                    resume_parts.extend(edu_parts)

            if len(resume_parts) > 1:
                parts.append("\n".join(resume_parts))

        # Предпочтения поиска
        pref_parts = ["ТЕКУЩИЙ ПОИСК:"]
        if preferences.query:
            pref_parts.append(f"- Ищет: {preferences.query}")
        if preferences.city:
            pref_parts.append(f"- Город поиска: {preferences.city}")
        if preferences.salary_from:
            pref_parts.append(f"- Зарплата от: {preferences.salary_from} ₽")
        if preferences.experience:
            pref_parts.append(f"- Требуемый опыт: {preferences.experience}")
        if preferences.employment_type:
            pref_parts.append(f"- Формат работы: {preferences.employment_type}")
        if preferences.exclude_keywords:
            pref_parts.append(f"- Не предлагать: {', '.join(preferences.exclude_keywords)}")

        if len(pref_parts) > 1:
            parts.append("\n".join(pref_parts))

        if not parts:
            return "Пока ничего не известно о пользователе."

        return "\n\n".join(parts)

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
- exclude_keywords: что ИСКЛЮЧИТЬ из поиска (массив строк) — это могут быть:
  * названия компаний (Пятёрочка, Магнит, Wildberries)
  * типы работ (курьер, продажи, холодные звонки)
  * адреса или районы
  * любые другие слова для исключения

ВАЖНО для exclude_keywords: Если пользователь говорит "убрать/убери/удали/не показывай/без" чего-то — добавь это в exclude_keywords.
Примеры:
- "убрать пятёрочку" -> exclude_keywords: ["пятёрочка"]
- "без магнита" -> exclude_keywords: ["магнит"]
- "не показывай вакансии с холодными звонками" -> exclude_keywords: ["холодные звонки"]

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
                    new_keywords = data["exclude_keywords"]
                    print(f"[Orchestrator] New exclude_keywords from message: {new_keywords}")
                    current.exclude_keywords.extend(new_keywords)

        except Exception as e:
            print(f"Error extracting preferences: {e}")
            import traceback
            traceback.print_exc()

        print(f"[Orchestrator] Current preferences: query={current.query}, city={current.city}, salary={current.salary_from}, exp={current.experience}, exclude={current.exclude_keywords}")
        return current


# Singleton
orchestrator = Orchestrator()
