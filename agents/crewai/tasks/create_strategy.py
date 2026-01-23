"""
Create Strategy Task - создание стратегии поиска.
"""
from crewai import Task, Agent


def create_strategy_task(
    agent: Agent,
    analysis_result: dict,
) -> Task:
    """
    Создать задачу формирования стратегии поиска.

    Эта задача:
    - Генерирует варианты поисковых запросов
    - Учитывает синонимы профессий
    - Определяет приоритеты поиска
    """

    # Параметры из анализа
    params = analysis_result.get("parameters", {})
    city = params.get("city", "не указан")
    professions = params.get("professions", [])
    salary_from = params.get("salary_from")
    work_format = params.get("work_format")

    professions_text = ", ".join(professions) if professions else "не указана"

    description = f"""
Создай стратегию поиска вакансий на основе параметров.

ПАРАМЕТРЫ ПОИСКА:
- Город: {city}
- Профессии: {professions_text}
- Зарплата от: {salary_from or "не указана"}
- Формат работы: {work_format or "любой"}

ЗАДАЧА:
1. Для КАЖДОЙ профессии сгенерируй 3-5 вариантов запросов:
   - Точное название
   - Синонимы (продавец = продавец-консультант = менеджер по продажам)
   - Смежные профессии

2. Учти отраслевую специфику:
   - ПВЗ → "сотрудник ПВЗ", "оператор пункта выдачи", "Wildberries", "OZON"
   - IT → конкретные технологии (Python, JavaScript, etc.)
   - Продавец → "продавец-консультант", "продавец-кассир", "менеджер торгового зала"

3. Определи приоритет источников:
   - database: быстрый поиск по базе (приоритет)
   - hh: HeadHunter
   - avito: Авито Работа

4. Если несколько профессий - план параллельного поиска

ФОРМАТ ОТВЕТА (JSON):
{{
    "search_queries": [
        {{
            "base_profession": "основная профессия",
            "queries": ["запрос1", "запрос2", "запрос3"],
            "priority": 1
        }}
    ],
    "search_config": {{
        "city": "город для поиска",
        "salary_from": число | null,
        "sources": ["database", "hh", "avito"],
        "use_live_search": true | false,
        "max_results_per_query": 20
    }},
    "reasoning": "объяснение стратегии"
}}
"""

    expected_output = """
JSON со стратегией поиска:
- search_queries: список запросов с вариантами
- search_config: конфигурация поиска
- reasoning: обоснование выбора стратегии
"""

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
    )
