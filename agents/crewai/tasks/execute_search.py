"""
Execute Search Task - выполнение поиска вакансий.
"""
from crewai import Task, Agent


def create_execute_search_task(
    agent: Agent,
    strategy: dict,
    use_live_search: bool = True,
) -> Task:
    """
    Создать задачу выполнения поиска вакансий.

    Эта задача:
    - Выполняет поиск по сгенерированным запросам
    - Использует VacancySearchTool и DatabaseSearchTool
    - Собирает результаты из разных источников
    """

    search_queries = strategy.get("search_queries", [])
    config = strategy.get("search_config", {})

    # Форматируем запросы
    queries_text = ""
    for sq in search_queries:
        base = sq.get("base_profession", "")
        queries = sq.get("queries", [])
        queries_text += f"\n{base}:\n"
        for q in queries:
            queries_text += f"  - {q}\n"

    tool_instruction = """
ИСПОЛЬЗУЙ ИНСТРУМЕНТЫ:
1. database_search - для быстрого поиска по базе (ВСЕГДА используй первым)
2. vacancy_search - для полного поиска включая live источники (hh, avito)

Пример вызова:
database_search(queries=["продавец", "продавец-консультант"], city="Москва", salary_from=50000)
vacancy_search(queries=["продавец"], city="Москва", salary_from=50000, include_live=True)
"""

    if not use_live_search:
        tool_instruction = """
ИСПОЛЬЗУЙ ИНСТРУМЕНТ:
database_search - для быстрого поиска по базе

Пример вызова:
database_search(queries=["продавец", "продавец-консультант"], city="Москва", salary_from=50000)

ВАЖНО: Live поиск отключён, используй только базу данных!
"""

    description = f"""
Выполни поиск вакансий по подготовленной стратегии.

ЗАПРОСЫ ДЛЯ ПОИСКА:
{queries_text}

КОНФИГУРАЦИЯ:
- Город: {config.get("city", "не указан")}
- Зарплата от: {config.get("salary_from", "не указана")}
- Максимум результатов на запрос: {config.get("max_results_per_query", 20)}

{tool_instruction}

ЗАДАЧА:
1. Выполни поиск по ВСЕМ запросам из стратегии
2. Сначала используй database_search для быстрых результатов
3. {"Затем vacancy_search для live источников" if use_live_search else "Live поиск отключён"}
4. Объедини результаты, удали дубликаты (по id или url)
5. Верни список найденных вакансий

ФОРМАТ ОТВЕТА (JSON):
{{
    "vacancies": [
        {{
            "id": "уникальный id",
            "title": "название вакансии",
            "company": "название компании",
            "city": "город",
            "salary_from": число | null,
            "salary_to": число | null,
            "url": "ссылка",
            "source": "hh" | "avito" | "database",
            "description": "краткое описание"
        }}
    ],
    "stats": {{
        "total_found": число,
        "from_database": число,
        "from_live": число,
        "queries_executed": число
    }}
}}
"""

    expected_output = """
JSON с результатами поиска:
- vacancies: список найденных вакансий
- stats: статистика поиска
"""

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
    )
