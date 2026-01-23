"""
Compose Response Task - формирование ответа пользователю.
"""
from crewai import Task, Agent


def create_compose_response_task(
    agent: Agent,
    request_type: str,
    analysis_result: dict = None,
    search_results: dict = None,
    validation_results: dict = None,
    user_name: str = None,
) -> Task:
    """
    Создать задачу формирования ответа пользователю.

    Эта задача:
    - Формирует понятный текстовый ответ
    - Адаптирует тон под ситуацию
    - Включает ключевую информацию
    """

    context_parts = []

    # Имя пользователя
    name_instruction = ""
    if user_name:
        name_instruction = f"Имя пользователя: {user_name}. Можешь обращаться по имени, но не в каждом сообщении."

    # Тип ответа и контекст
    if request_type == "CLARIFICATION":
        params = analysis_result.get("parameters", {}) if analysis_result else {}
        question = analysis_result.get("clarification_question", "") if analysis_result else ""

        context_parts.append(f"""
ТИП: УТОЧНЯЮЩИЙ ВОПРОС

Известные параметры:
- Город: {params.get("city", "не указан")}
- Профессия: {", ".join(params.get("professions", [])) or "не указана"}
- Зарплата: {params.get("salary_from", "не указана")}

Нужно спросить:
{question}
""")

    elif request_type == "SEARCH":
        params = analysis_result.get("parameters", {}) if analysis_result else {}
        stats = validation_results.get("stats", {}) if validation_results else {}

        context_parts.append(f"""
ТИП: РЕЗУЛЬТАТЫ ПОИСКА

Параметры поиска:
- Город: {params.get("city", "не указан")}
- Профессии: {", ".join(params.get("professions", [])) or "не указаны"}
- Зарплата от: {params.get("salary_from", "не указана")}

Результаты:
- Всего найдено: {stats.get("total", 0)}
- Прошли валидацию: {stats.get("accepted", 0)}
- Рейтинг A (отличные): {stats.get("rating_a", 0)}
- Рейтинг B (хорошие): {stats.get("rating_b", 0)}
- Рейтинг C (может подойти): {stats.get("rating_c", 0)}
""")

    elif request_type == "CHAT":
        context_parts.append("""
ТИП: ОБЫЧНЫЙ ДИАЛОГ

Пользователь задал вопрос или пообщался. Ответь дружелюбно,
но направь разговор к поиску работы если уместно.
""")

    elif request_type == "PROFILE":
        context_parts.append("""
ТИП: ВОПРОС О ПРОФИЛЕ

Пользователь спрашивает о своём профиле или резюме.
Объясни что можно сделать и как это поможет в поиске.
""")

    context_text = "\n".join(context_parts)

    description = f"""
Сформируй ответ пользователю на основе результатов работы.

{name_instruction}

КОНТЕКСТ:
{context_text}

ПРАВИЛА ОТВЕТА:

1. КРАТКОСТЬ:
   - 1-3 предложения для основного сообщения
   - Детали вакансий - в карточках, не в тексте
   - Не нужно перечислять все вакансии

2. КОНКРЕТИКА:
   - "Нашёл 23 вакансии" вместо "нашёл несколько вариантов"
   - "от 60 до 120 тысяч" вместо "с хорошей зарплатой"
   - Конкретные цифры и факты

3. ТОН:
   - Дружелюбный, но профессиональный
   - Без канцеляризмов и официоза
   - Позитивный, даже если результатов мало

4. СТРУКТУРА при поиске:
   "Нашёл X вакансий в [город] [профессия]. [Краткое резюме]."

5. СТРУКТУРА при уточнении:
   "[Подтвердить что понял]. [ОДИН вопрос]"
   Пример: "Понял, ищем в Москве от 80 тысяч. А в какой сфере хочешь работать?"

6. ЕСЛИ МАЛО РЕЗУЛЬТАТОВ (<5):
   - НЕ извиняйся
   - Предложи расширить поиск
   - "Нашёл 3 вакансии. Хочешь посмотреть похожие профессии?"

7. ЗАПРЕЩЕНО:
   - Длинные вступления
   - "Здравствуйте! Я рад помочь..."
   - Извинения за малое количество
   - Технические детали (source: hh, database)
   - Шаблонные фразы

ФОРМАТ ОТВЕТА (JSON):
{{
    "response_text": "текст ответа пользователю",
    "show_vacancies": true | false,
    "suggested_actions": ["действие1", "действие2"] | null
}}
"""

    expected_output = """
JSON с ответом:
- response_text: текст для отображения пользователю
- show_vacancies: показывать ли карточки вакансий
- suggested_actions: предложенные действия (опционально)
"""

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
    )
