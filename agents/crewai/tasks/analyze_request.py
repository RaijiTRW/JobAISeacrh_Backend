"""
Analyze Request Task - анализ запроса пользователя.
"""
from crewai import Task, Agent


def create_analyze_request_task(
    agent: Agent,
    user_message: str,
    user_profile: dict,
    conversation_history: list[dict],
    context_summary: str,
) -> Task:
    """
    Создать задачу анализа запроса пользователя.

    Эта задача:
    - Извлекает параметры поиска из сообщения
    - Учитывает профиль пользователя
    - Определяет нужны ли уточнения
    """

    # Форматируем историю для контекста
    history_text = ""
    if conversation_history:
        recent = conversation_history[-6:]  # Последние 6 сообщений
        for msg in recent:
            role = "Пользователь" if msg.get("role") == "user" else "Ассистент"
            history_text += f"{role}: {msg.get('content', '')}\n"

    # Форматируем профиль
    profile_text = "Нет данных о профиле"
    if user_profile:
        parts = []
        if user_profile.get("name"):
            parts.append(f"Имя: {user_profile['name']}")
        if user_profile.get("city"):
            parts.append(f"Город: {user_profile['city']}")
        if user_profile.get("desired_position"):
            parts.append(f"Желаемая должность: {user_profile['desired_position']}")
        if user_profile.get("desired_salary"):
            parts.append(f"Желаемая зарплата: {user_profile['desired_salary']}")
        if user_profile.get("experience"):
            parts.append(f"Опыт: {user_profile['experience']}")
        if parts:
            profile_text = "\n".join(parts)

    description = f"""
Проанализируй запрос пользователя и извлеки параметры поиска работы.

СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ:
{user_message}

ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:
{profile_text}

КОНТЕКСТ ДИАЛОГА:
{context_summary or "Новый диалог"}

ИСТОРИЯ СООБЩЕНИЙ:
{history_text or "Нет истории"}

ЗАДАЧА:
1. Извлеки все параметры поиска:
   - Город (обязателен для поиска)
   - Профессия/должность (обязательна для поиска)
   - Минимальная зарплата (опционально)
   - Формат работы: удалёнка/офис/гибрид (опционально)
   - Тип занятости: полная/частичная (опционально)
   - Дополнительные требования

2. Определи тип запроса:
   - SEARCH: пользователь хочет найти вакансии
   - CLARIFICATION: нужны уточнения (не хватает обязательных параметров)
   - CHAT: просто разговор, не поиск
   - PROFILE: вопросы о профиле/резюме

3. Если CLARIFICATION - сформулируй ОДИН конкретный вопрос

4. Учти информацию из профиля если параметр не указан явно

5. Если пользователь указал несколько профессий - перечисли все!

ФОРМАТ ОТВЕТА (JSON):
{{
    "request_type": "SEARCH" | "CLARIFICATION" | "CHAT" | "PROFILE",
    "parameters": {{
        "city": "название города" | null,
        "professions": ["профессия1", "профессия2"] | null,
        "salary_from": число | null,
        "work_format": "remote" | "office" | "hybrid" | null,
        "employment_type": "full" | "part" | null,
        "additional": "дополнительные требования" | null
    }},
    "clarification_question": "вопрос если нужно" | null,
    "confidence": 0.0-1.0,
    "reasoning": "объяснение решения"
}}
"""

    expected_output = """
JSON с результатом анализа:
- request_type: тип запроса
- parameters: извлечённые параметры поиска
- clarification_question: вопрос для уточнения (если нужен)
- confidence: уверенность в понимании запроса
- reasoning: обоснование
"""

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
    )
