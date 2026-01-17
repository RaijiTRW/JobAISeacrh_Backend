"""
Модератор вакансий работодателей - проверяет на запрещенный контент
"""
import json
from typing import Optional, Dict
import httpx
from config import settings


class EmployerVacancyModerator:
    """AI модератор для вакансий работодателей"""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=60.0)
        self.api_key = settings.OPENROUTER_API_KEY
        self.base_url = settings.OPENROUTER_BASE_URL
        self.model = settings.MODEL_NAME

    async def check_vacancy(
        self,
        title: str,
        company: str,
        description: str,
        requirements: Optional[str] = None,
        conditions: Optional[str] = None,
    ) -> Dict[str, any]:
        """
        Проверка вакансии на соответствие правилам платформы.

        Returns:
            {
                "approved": bool,
                "reason": str (если не одобрено)
            }
        """
        vacancy_data = {
            "title": title,
            "company": company,
            "description": description,
            "requirements": requirements or "",
            "conditions": conditions or "",
        }

        prompt = self._build_prompt(vacancy_data)

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
                print(f"[EmployerVacancyModerator] API error: {response.status_code}")
                # При ошибке API - одобряем (чтобы не заблокировать легитимные вакансии)
                # Админ проверит вручную
                return {"approved": True}

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # Парсим ответ AI
            moderation_result = self._parse_response(content)

            approved = moderation_result.get("approved", True)
            reason = moderation_result.get("reason", "")

            if not approved:
                print(f"[EmployerVacancyModerator] Rejected: '{title}' - {reason}")
            else:
                print(f"[EmployerVacancyModerator] Approved: '{title}'")

            return {"approved": approved, "reason": reason}

        except Exception as e:
            print(f"[EmployerVacancyModerator] Error: {e}")
            # При ошибке - одобряем, админ проверит
            return {"approved": True}

    def _build_prompt(self, vacancy_data: Dict) -> str:
        """Построение промпта для AI"""
        return f"""Ты - модератор платформы поиска работы. Проверь вакансию на соответствие правилам платформы.

ЗАПРЕЩЕНО:
1. **Военная тематика** (СТРОГО запрещено):
   - БПЛА, дроны, беспилотники, операторы БПЛА
   - Военнослужащий, контрактная служба, контракт СВО
   - Участники СВО, участники боевых действий
   - Военкомат, военная часть, казарма
   - Армия, войска, обороны
   - Мобилизация, военное обучение
   - Любые вакансии, связанные с военной службой

2. **Мошенничество**:
   - Пирамиды, MLM без чёткого описания продукта
   - "Заработай миллион за месяц"
   - Требование денег за обучение/вступление
   - Подозрительно высокие обещания заработка
   - "Работа без опыта за 200,000₽/месяц"

3. **Неприемлемый контент**:
   - Нецензурная лексика
   - Дискриминация (по полу, возрасту, национальности)
   - Незаконная деятельность
   - Эскорт-услуги, казино, ставки

4. **Не связано с работой**:
   - Реклама товаров/услуг вместо вакансии
   - Спам, продажа курсов
   - Криптовалютные схемы без реального бизнеса

РАЗРЕШЕНО:
- Обычные вакансии (офис, IT, продажи, строительство и т.д.)
- Удалённая работа
- Фриланс проекты
- Честные MLM компании с чётким описанием продукта
- Вакансии с испытательным сроком

Проверь следующую вакансию:

**Название**: {vacancy_data['title']}
**Компания**: {vacancy_data['company']}
**Описание**: {vacancy_data['description']}
**Требования**: {vacancy_data['requirements']}
**Условия**: {vacancy_data['conditions']}

Верни JSON в формате:
{{
  "approved": true/false,
  "reason": "краткая причина отклонения на русском (если отклонено)"
}}

ВАЖНО:
- Возвращай ТОЛЬКО валидный JSON, без комментариев
- Будь строгим к военной тематике - любое упоминание = approved: false
- Будь строгим к мошенничеству
- Будь лояльным к обычным вакансиям
- Если сомневаешься в обычной вакансии - лучше одобри (approved: true)
- Причина должна быть понятна работодателю и конкретна"""

    def _parse_response(self, content: str) -> Dict:
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
            print(f"[EmployerVacancyModerator] JSON parse error: {e}")
            print(f"[EmployerVacancyModerator] Response: {content}")
            return {"approved": True}  # При ошибке парсинга - одобряем
