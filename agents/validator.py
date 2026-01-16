"""
Валидатор вакансий
AI-фильтрация с confidence scoring и feedback loop
"""

import httpx
import json
from dataclasses import dataclass, field
from models.vacancy import Vacancy
from models.chat import UserPreferences
from config import get_settings


@dataclass
class VacancyScore:
    """Оценка одной вакансии"""
    vacancy: Vacancy
    confidence: float  # 0.0 - 1.0
    reason: str  # Почему подходит/не подходит


@dataclass
class ValidationResult:
    """Результат валидации вакансий"""
    validated: list[Vacancy]
    rejected: list[Vacancy]
    suggested_keywords: list[str] = field(default_factory=list)  # Для feedback loop


class VacancyValidator:
    """AI-валидатор с confidence scoring"""

    def __init__(self):
        self.settings = get_settings()

    async def validate_batch(
        self,
        vacancies: list[Vacancy],
        preferences: UserPreferences,
        queries: list[str] = None,
        required_city: str = None,
    ) -> ValidationResult:
        """AI валидация с confidence scoring"""
        if not vacancies:
            return ValidationResult(validated=[], rejected=[], suggested_keywords=[])

        queries = queries or []
        queries_text = " ".join(queries).lower()

        print(f"[Validator] Input: {len(vacancies)} vacancies")
        print(f"[Validator] User query: {queries_text}")
        print(f"[Validator] Required city: {required_city}")

        # Пользовательские исключения
        user_exclusions = [w.lower().strip() for w in preferences.exclude_keywords if w]

        # Нормализация города для сравнения
        def normalize_city(city: str) -> str:
            if not city:
                return ""
            city = city.lower().strip()
            # Убираем окончания и приводим к базовой форме
            replacements = {
                "санкт-петербург": "спб",
                "петербург": "спб",
                "ленинград": "спб",
                "с-петербург": "спб",
                "москва": "москва",
                "мск": "москва",
                "новороссийск": "новороссийск",
                "новоросс": "новороссийск",
                "краснодар": "краснодар",
                "ростов-на-дону": "ростов",
                "ростов на дону": "ростов",
                "ростов-на-дону": "ростов",
                "нижний новгород": "нижний новгород",
                "н.новгород": "нижний новгород",
                "екатеринбург": "екатеринбург",
                "екб": "екатеринбург",
                "новосибирск": "новосибирск",
                "нск": "новосибирск",
                "казань": "казань",
                "самара": "самара",
                "челябинск": "челябинск",
                "омск": "омск",
                "уфа": "уфа",
                "красноярск": "красноярск",
                "воронеж": "воронеж",
                "волгоград": "волгоград",
                "мурманск": "мурманск",
                "сочи": "сочи",
                "анапа": "анапа",
                "геленджик": "геленджик",
            }
            for full, short in replacements.items():
                if full in city or city in full:
                    return short
            return city

        required_city_normalized = normalize_city(required_city) if required_city else None

        # Pre-фильтр: город, исключения пользователя, зарплата
        to_validate = []
        user_rejected = []

        for vacancy in vacancies:
            # === ПРОВЕРКА ГОРОДА ===
            if required_city_normalized:
                vacancy_city = normalize_city(vacancy.city)
                title_lower = (vacancy.title or "").lower()
                desc_lower = (vacancy.description or "").lower()

                # Проверяем на удалёнку (СТРОГО - только если явно указано "полностью удалённо")
                # Не считаем удалённой, если просто "возможна удалённая работа"
                remote_keywords_strict = [
                    "полностью удал",
                    "100% удал",
                    "только удал",
                    "remote only",
                    "из любого города",
                    "из любой точки",
                    "работа на дому",
                    "home office",
                ]
                is_remote = any(word in title_lower for word in remote_keywords_strict)

                # Если город вакансии не указан
                if not vacancy_city:
                    # Пропускаем только если это явно удалёнка
                    if not is_remote:
                        print(f"[Validator] No city in vacancy: '{vacancy.title}' (need '{required_city}')")
                        user_rejected.append(vacancy)
                        continue
                # Если город указан - сравниваем
                elif required_city_normalized not in vacancy_city and vacancy_city not in required_city_normalized:
                    # Разрешаем удалённые вакансии
                    if not is_remote:
                        print(f"[Validator] City mismatch: '{vacancy.title}' in '{vacancy.city}' (need '{required_city}')")
                        user_rejected.append(vacancy)
                        continue

            # === ПРОВЕРКА ИСКЛЮЧЕНИЙ ===
            if user_exclusions:
                full_text = f"{(vacancy.title or '').lower()} {(vacancy.description or '').lower()}"
                excluded = False
                for excl in user_exclusions:
                    if excl in full_text:
                        print(f"[Validator] User excluded: '{vacancy.title}' - contains '{excl}'")
                        excluded = True
                        break
                if excluded:
                    user_rejected.append(vacancy)
                    continue

            # === ПРОВЕРКА ЗАРПЛАТЫ ===
            if preferences.salary_from:
                if vacancy.salary_to and vacancy.salary_to < preferences.salary_from:
                    user_rejected.append(vacancy)
                    continue

            to_validate.append(vacancy)

        print(f"[Validator] After user filters: {len(to_validate)} to AI, {len(user_rejected)} rejected")

        if not to_validate:
            return ValidationResult(validated=[], rejected=user_rejected, suggested_keywords=[])

        # AI валидация с confidence
        ai_result = await self._ai_validate_with_confidence(to_validate, queries_text, required_city)

        # Объединяем rejected
        all_rejected = user_rejected + ai_result.rejected

        print(f"[Validator] Final: {len(ai_result.validated)} validated, {len(all_rejected)} rejected")
        print(f"[Validator] Suggested keywords: {ai_result.suggested_keywords}")

        return ValidationResult(
            validated=ai_result.validated,
            rejected=all_rejected,
            suggested_keywords=ai_result.suggested_keywords
        )

    async def _ai_validate_with_confidence(
        self, vacancies: list[Vacancy], user_query: str, required_city: str = None
    ) -> ValidationResult:
        """AI оценивает каждую вакансию с confidence score"""
        validated = []
        rejected = []
        suggested_keywords = []
        batch_size = 15  # Меньше батч для лучшего качества

        for i in range(0, len(vacancies), batch_size):
            batch = vacancies[i:i + batch_size]
            scores, keywords = await self._ask_ai_confidence(batch, user_query, required_city)

            suggested_keywords.extend(keywords)

            for vacancy in batch:
                score = scores.get(vacancy.id)
                if score and score["confidence"] >= 0.5:
                    print(f"[Validator] YES ({score['confidence']:.1f}): '{vacancy.title}' - {score['reason']}")
                    validated.append(vacancy)
                else:
                    reason = score["reason"] if score else "не оценено"
                    conf = score["confidence"] if score else 0
                    print(f"[Validator] NO ({conf:.1f}): '{vacancy.title}' - {reason}")
                    rejected.append(vacancy)

        # Убираем дубликаты из keywords
        unique_keywords = list(set(suggested_keywords))[:10]

        return ValidationResult(
            validated=validated,
            rejected=rejected,
            suggested_keywords=unique_keywords
        )

    async def _ask_ai_confidence(
        self, vacancies: list[Vacancy], user_query: str, required_city: str = None
    ) -> tuple[dict, list[str]]:
        """AI возвращает confidence score для каждой вакансии"""

        vacancy_list = []
        for v in vacancies:
            desc = (v.description[:200] if v.description else '').replace('\n', ' ')
            vacancy_list.append(f"ID: {v.id}\nНазвание: {v.title}\nГород: {v.city}\nКомпания: {v.company}\nОписание: {desc}\n")

        city_info = f"\nТребуемый город: {required_city}" if required_city else ""
        prompt = f"""Человек ищет работу: "{user_query}"{city_info}

Оцени каждую вакансию — насколько она подходит под запрос.

ПРАВИЛА:
1. Смотри на СУТЬ, не на точное совпадение слов
2. "ПВЗ" = "пункт выдачи" = "выдача заказов" = "Wildberries/Ozon пункт"
3. Если вакансия МОЖЕТ подойти — ставь confidence >= 0.5
4. Отсеивай только явно НЕ ТО (курьер когда ищут ПВЗ, продавец когда ищут программиста)
5. ВАЖНО: Город вакансии должен совпадать с требуемым городом (кроме 100% удалённых вакансий)

ВАКАНСИИ:
{chr(10).join(vacancy_list)}

Ответь СТРОГО в JSON формате:
{{
  "scores": [
    {{"id": "vacancy_id", "confidence": 0.9, "reason": "точное совпадение"}},
    {{"id": "vacancy_id2", "confidence": 0.3, "reason": "это курьер, не ПВЗ"}}
  ],
  "suggested_keywords": ["ключевое слово 1", "ключевое слово 2"]
}}

suggested_keywords — слова из ПОДХОДЯЩИХ вакансий, которые можно использовать для расширения поиска.
Например если нашёл "оператор склада WB" — добавь "оператор склада", "WB"."""

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
                        "max_tokens": 2000,
                        "temperature": 0.1,
                    },
                    timeout=60.0,
                )

                if response.status_code != 200:
                    print(f"[Validator] AI error {response.status_code}")
                    raise Exception(f"AI validator error: {response.status_code}")

                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                print(f"[Validator] AI response: {content[:300]}...")

                # Парсим JSON
                try:
                    # Ищем JSON в ответе
                    start = content.find("{")
                    end = content.rfind("}") + 1
                    if start >= 0 and end > start:
                        json_str = content[start:end]
                        result = json.loads(json_str)

                        scores = {}
                        for item in result.get("scores", []):
                            scores[item["id"]] = {
                                "confidence": float(item.get("confidence", 0.5)),
                                "reason": item.get("reason", "")
                            }

                        keywords = result.get("suggested_keywords", [])
                        return scores, keywords

                except json.JSONDecodeError as e:
                    print(f"[Validator] JSON parse error: {e}")
                    # Fallback: одобряем всё
                    scores = {v.id: {"confidence": 0.6, "reason": "fallback"} for v in vacancies}
                    return scores, []

        except Exception as e:
            print(f"[Validator] Error: {e}")
            raise

        return {}, []


# Singleton
validator = VacancyValidator()
