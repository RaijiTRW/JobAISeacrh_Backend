"""
Validate Results Task - валидация найденных вакансий.
"""
from crewai import Task, Agent


def create_validate_results_task(
    agent: Agent,
    vacancies: list[dict],
    original_request: dict,
) -> Task:
    """
    Создать задачу валидации вакансий.

    Эта задача:
    - Проверяет релевантность вакансий
    - Фильтрует мошенничество и запрещённый контент
    - Ранжирует по качеству
    """

    params = original_request.get("parameters", {})
    city = params.get("city", "не указан")
    professions = params.get("professions", [])
    salary_from = params.get("salary_from")

    professions_text = ", ".join(professions) if professions else "не указана"

    # Сокращаем список вакансий для промпта
    vacancies_preview = vacancies[:30] if len(vacancies) > 30 else vacancies
    vacancies_text = ""
    for i, v in enumerate(vacancies_preview):
        vacancies_text += f"""
{i+1}. {v.get('title', 'Без названия')}
   Компания: {v.get('company', 'Не указана')}
   Город: {v.get('city', 'Не указан')}
   Зарплата: {v.get('salary_from', '?')} - {v.get('salary_to', '?')}
   ID: {v.get('id', '')}
"""

    description = f"""
Проверь качество и релевантность найденных вакансий.

ИСХОДНЫЙ ЗАПРОС:
- Город: {city}
- Профессии: {professions_text}
- Зарплата от: {salary_from or "не указана"}

НАЙДЕННЫЕ ВАКАНСИИ ({len(vacancies)} шт):
{vacancies_text}
{"... и ещё " + str(len(vacancies) - 30) + " вакансий" if len(vacancies) > 30 else ""}

КРИТЕРИИ ВАЛИДАЦИИ:

1. РЕЛЕВАНТНОСТЬ (проверь соответствие):
   - Город совпадает или близко (Московская область для Москвы - ОК)
   - Профессия соответствует запросу (учти синонимы!)
   - Зарплата >= минимальной (если указана)

2. КРАСНЫЕ ФЛАГИ (отклонить!):
   - Военная тематика: армия, контракт, военная служба, СВО
   - Явное мошенничество: "заработок от 500к", "без опыта от 200к"
   - Сетевой маркетинг: Amway, Herbalife, NL
   - Эскорт, сомнительные услуги
   - Требование предоплаты от кандидата

3. ЖЁЛТЫЕ ФЛАГИ (понизить рейтинг):
   - Зарплата не указана
   - Слишком общее описание
   - Компания не указана

4. РЕЙТИНГ:
   - A: отличное соответствие
   - B: хорошее соответствие
   - C: может подойти
   - REJECT: не прошёл валидацию

ВАЖНО: Не будь слишком строгим!
- Если вакансия "может подойти" - ставь C, не отклоняй
- Валидатор НЕ должен решать за пользователя

ФОРМАТ ОТВЕТА (JSON):
{{
    "validated": [
        {{
            "id": "id вакансии",
            "rating": "A" | "B" | "C",
            "reason": "причина рейтинга"
        }}
    ],
    "rejected": [
        {{
            "id": "id вакансии",
            "reason": "причина отклонения"
        }}
    ],
    "stats": {{
        "total": число,
        "accepted": число,
        "rejected": число,
        "rating_a": число,
        "rating_b": число,
        "rating_c": число
    }}
}}
"""

    expected_output = """
JSON с результатами валидации:
- validated: список прошедших валидацию с рейтингом
- rejected: список отклонённых с причинами
- stats: статистика валидации
"""

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
    )
