"""
Валидатор вакансий
AI-фильтрация с пониманием контекста
"""

import httpx
from dataclasses import dataclass
from models.vacancy import Vacancy
from models.chat import UserPreferences
from config import get_settings


@dataclass
class ValidationResult:
    """Результат валидации вакансий"""
    validated: list[Vacancy]
    rejected: list[Vacancy]


class VacancyValidator:
    """AI-валидатор — сам читает и понимает каждую вакансию"""

    def __init__(self):
        self.settings = get_settings()

    async def validate_batch(
        self,
        vacancies: list[Vacancy],
        preferences: UserPreferences,
        queries: list[str] = None,
    ) -> ValidationResult:
        """AI сам решает какие вакансии подходят"""
        if not vacancies:
            return ValidationResult(validated=[], rejected=[])

        queries = queries or []
        queries_text = " ".join(queries).lower()

        print(f"[Validator] Input: {len(vacancies)} vacancies")
        print(f"[Validator] User query: {queries_text}")

        # Пользовательские исключения (только если пользователь сам попросил)
        user_exclusions = [w.lower().strip() for w in preferences.exclude_keywords if w]

        # Фильтруем только по явным исключениям пользователя
        to_validate = []
        user_rejected = []

        for vacancy in vacancies:
            # Проверяем только пользовательские исключения
            if user_exclusions:
                full_text = f"{(vacancy.title or '').lower()} {(vacancy.description or '').lower()} {(vacancy.company or '').lower()}"
                excluded = False
                for excl in user_exclusions:
                    if excl in full_text:
                        print(f"[Validator] User excluded: '{vacancy.title}' - contains '{excl}'")
                        excluded = True
                        break
                if excluded:
                    user_rejected.append(vacancy)
                    continue

            # Проверка зарплаты (если пользователь указал минимум)
            if preferences.salary_from:
                if vacancy.salary_to and vacancy.salary_to < preferences.salary_from:
                    user_rejected.append(vacancy)
                    continue

            to_validate.append(vacancy)

        print(f"[Validator] After user filters: {len(to_validate)} to AI, {len(user_rejected)} rejected by user prefs")

        if not to_validate:
            return ValidationResult(validated=[], rejected=user_rejected)

        # AI сам решает для каждой вакансии
        ai_result = await self._ai_validate(to_validate, queries_text)

        # Объединяем rejected
        all_rejected = user_rejected + ai_result.rejected

        print(f"[Validator] Final: {len(ai_result.validated)} validated, {len(all_rejected)} rejected")
        return ValidationResult(validated=ai_result.validated, rejected=all_rejected)

    async def _ai_validate(self, vacancies: list[Vacancy], user_query: str) -> ValidationResult:
        """AI читает каждую вакансию и решает подходит ли она"""
        validated = []
        rejected = []
        batch_size = 20  # Больше за раз - меньше запросов

        for i in range(0, len(vacancies), batch_size):
            batch = vacancies[i:i + batch_size]
            approved_ids = await self._ask_ai(batch, user_query)

            for vacancy in batch:
                if vacancy.id in approved_ids:
                    print(f"[Validator] AI YES: '{vacancy.title}'")
                    validated.append(vacancy)
                else:
                    print(f"[Validator] AI NO: '{vacancy.title}'")
                    rejected.append(vacancy)

        return ValidationResult(validated=validated, rejected=rejected)

    async def _ask_ai(self, vacancies: list[Vacancy], user_query: str) -> set[str]:
        """Спрашиваем AI какие вакансии подходят под запрос пользователя"""

        # Формируем список вакансий
        vacancy_list = []
        for i, v in enumerate(vacancies):
            desc = (v.description[:300] if v.description else 'нет описания').replace('\n', ' ')
            vacancy_list.append(f"{i+1}. ID={v.id} | {v.title} | {v.company} | {desc}")

        prompt = f"""Пользователь ищет работу: "{user_query}"

Прочитай каждую вакансию и реши — подходит ли она под запрос пользователя.

ВАЖНО: Будь ЛОЯЛЬНЫМ! Если вакансия МОЖЕТ подойти — включай её.
- Не отсеивай вакансии только потому что название немного отличается
- Если в описании/компании есть связь с запросом — включай
- В сомнительных случаях — ВКЛЮЧАЙ (лучше показать лишнее чем пропустить нужное)

Вакансии:
{chr(10).join(vacancy_list)}

Ответь ТОЛЬКО JSON массивом ID подходящих вакансий:
["id1", "id2", ...]

Если подходят ВСЕ — верни все ID.
Если не подходит НИ ОДНА — верни []"""

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.settings.openrouter_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.validator_model_name,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1000,
                        "temperature": 0.1,  # Немного вариативности для лояльности
                    },
                    timeout=60.0,
                )

                if response.status_code != 200:
                    print(f"[Validator] AI error {response.status_code}")
                    raise Exception(f"AI validator error: {response.status_code}")

                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "[]")
                print(f"[Validator] AI response: {content[:200]}...")

                # Парсим JSON
                import json
                try:
                    start = content.find("[")
                    end = content.rfind("]") + 1
                    if start >= 0 and end > start:
                        json_str = content[start:end]
                        approved_ids = json.loads(json_str)
                        return set(approved_ids)
                except json.JSONDecodeError as e:
                    print(f"[Validator] JSON parse error: {content}")
                    raise Exception(f"AI response parse error: {e}")

        except Exception as e:
            print(f"[Validator] Error: {e}")
            raise


# Singleton
validator = VacancyValidator()
