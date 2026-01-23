"""
Агент модерации контента - проверяет вакансии на запрещённый контент
"""
import json
from typing import List, Dict
import httpx
from config import get_settings
from models.vacancy import Vacancy


class ContentModerator:
    """Модератор контента с использованием AI"""

    def __init__(self):
        settings = get_settings()
        self.client = httpx.AsyncClient(timeout=60.0)
        self.api_key = settings.openrouter_api_key
        self.base_url = settings.openrouter_base_url
        self.model = settings.model_name

    async def check_vacancies(
        self, vacancies: List[Vacancy]
    ) -> Dict[str, List[Vacancy]]:
        """
        Проверка списка вакансий на запрещённый контент.

        Returns:
            {
                "approved": [вакансии, прошедшие модерацию],
                "rejected": [вакансии с запрещённым контентом]
            }
        """
        if not vacancies:
            return {"approved": [], "rejected": []}

        # Подготовка данных для AI
        vacancies_data = []
        for v in vacancies:
            vacancies_data.append({
                "id": v.id,
                "title": v.title or "",
                "description": v.description or "",
                "company": v.company or "",
            })

        prompt = self._build_prompt(vacancies_data)

        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )

            if response.status_code != 200:
                print(f"[ContentModerator] API error: {response.status_code}")
                # При ошибке API - одобряем все (чтобы не потерять легитимные вакансии)
                return {"approved": vacancies, "rejected": []}

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # Парсим ответ AI
            moderation_results = self._parse_response(content)

            # Разделяем вакансии
            approved = []
            rejected = []

            for vacancy in vacancies:
                result = moderation_results.get(vacancy.id, {"is_forbidden": False})
                if result.get("is_forbidden", False):
                    print(
                        f"[ContentModerator] Rejected: '{vacancy.title}' - {result.get('reason', 'unknown')}"
                    )
                    rejected.append(vacancy)
                else:
                    approved.append(vacancy)

            print(
                f"[ContentModerator] Checked {len(vacancies)} vacancies: {len(approved)} approved, {len(rejected)} rejected"
            )

            return {"approved": approved, "rejected": rejected}

        except Exception as e:
            print(f"[ContentModerator] Error: {e}")
            # При ошибке - одобряем все
            return {"approved": vacancies, "rejected": []}

    def _build_prompt(self, vacancies_data: List[Dict]) -> str:
        """Построение промпта для AI"""
        return f"""Ты - модератор контента платформы поиска работы. Твоя задача - проверить вакансии на наличие ЗАПРЕЩЁННОГО контента.

ЗАПРЕЩЁННЫЕ темы (вакансии с этими темами ДОЛЖНЫ быть отклонены):
1. Военная служба и всё связанное с военной тематикой:
   - БПЛА, дроны, беспилотники, операторы БПЛА
   - Военнослужащий, контрактная служба, контракт СВО
   - Участники СВО, участники боевых действий
   - Военкомат, военная часть, казарма
   - Армия, войска, обороны
   - Мобилизация
   - Военное обучение, военная подготовка
   - Любые вакансии, связанные с военной службой

2. НЕ запрещённые вакансии (примеры того, что МОЖНО):
   - Гражданская авиация (пилоты гражданских авиалиний, диспетчеры)
   - IT специалисты (даже если упоминается работа с дронами в гражданских целях)
   - Обычные рабочие вакансии (строители, водители, менеджеры и т.д.)

Проверь следующие вакансии:

{json.dumps(vacancies_data, ensure_ascii=False, indent=2)}

Верни JSON в формате:
{{
  "vacancy_id_1": {{
    "is_forbidden": true/false,
    "reason": "краткая причина (если запрещено)"
  }},
  "vacancy_id_2": {{
    "is_forbidden": true/false,
    "reason": "краткая причина (если запрещено)"
  }},
  ...
}}

ВАЖНО:
- Возвращай ТОЛЬКО валидный JSON, без комментариев и дополнительного текста
- Будь строгим к военной тематике - любое упоминание военной службы = is_forbidden: true
- Будь лояльным к гражданским вакансиям - не отклоняй обычные вакансии
- Если сомневаешься - лучше одобри (is_forbidden: false)"""

    def _parse_response(self, content: str) -> Dict[str, Dict]:
        """Парсинг ответа AI"""
        try:
            # Убираем markdown разметку если есть
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            result = json.loads(content)
            return result
        except json.JSONDecodeError as e:
            print(f"[ContentModerator] JSON parse error: {e}")
            print(f"[ContentModerator] Response: {content}")
            return {}
