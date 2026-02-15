"""
API эндпоинты для Resume Builder
Улучшение текста резюме с помощью AI (OpenRouter)
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import json

from agents.crewai.llm_config import get_main_llm
from services.subscription_service import subscription_service
from config import get_settings

router = APIRouter(prefix="/resume", tags=["resume"])
settings = get_settings()


class AIImproveRequest(BaseModel):
    """Запрос на улучшение текста резюме"""
    text: str
    field: str  # about, experience_description, skills, achievements
    context: Optional[str] = None
    user_id: str


class AIImproveResponse(BaseModel):
    """Ответ с улучшенным текстом"""
    improved_text: str
    suggestions: List[str]
    ats_keywords: List[str]
    explanation: Optional[str] = None


@router.post("/ai-improve", response_model=AIImproveResponse)
async def improve_resume_text(request: AIImproveRequest):
    """
    Улучшение текста резюме с помощью AI.

    Проверяет подписку пользователя и использует OpenRouter
    для улучшения текста в соответствии с российскими стандартами.
    """
    # Проверяем, есть ли у пользователя доступ к AI
    can_use_ai = await _check_ai_access(request.user_id)
    if not can_use_ai:
        raise HTTPException(
            status_code=403,
            detail="AI функции доступны только на Pro подписке"
        )

    # Получаем улучшенный текст
    result = await _improve_text_with_ai(
        text=request.text,
        field=request.field,
        context=request.context
    )

    return result


async def _check_ai_access(user_id: str) -> bool:
    """Проверка доступа к AI функциям"""
    try:
        status = await subscription_service.get_full_status(user_id)
        # AI доступен на Pro и Pro Trial
        if status.subscription:
            return status.subscription.is_pro or status.subscription.is_pro_trial
        return False
    except Exception as e:
        print(f"[Resume] Error checking AI access: {e}")
        return False


async def _improve_text_with_ai(
    text: str,
    field: str,
    context: Optional[str]
) -> AIImproveResponse:
    """Улучшение текста через OpenRouter"""

    system_prompt = _get_system_prompt_for_field(field)

    user_prompt = f"Улучши следующий текст для раздела '{_get_field_name(field)}':\n\n{text}"

    if context:
        user_prompt += f"\n\nКонтекст: {context}"

    try:
        llm = get_main_llm()
        response = await llm.chat(
            system_prompt=system_prompt,
            user_message=user_prompt,
            temperature=0.3,  # Более детерминированный для редактирования
            max_tokens=2000,
            json_mode=True
        )

        result_data = json.loads(response)

        return AIImproveResponse(
            improved_text=result_data.get("improved_text", text),
            suggestions=result_data.get("suggestions", []),
            ats_keywords=result_data.get("ats_keywords", []),
            explanation=result_data.get("explanation")
        )

    except json.JSONDecodeError as e:
        print(f"[Resume] JSON decode error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Не удалось обработать ответ AI"
        )
    except Exception as e:
        print(f"[Resume] AI improvement error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при улучшении текста: {str(e)}"
        )


def _get_system_prompt_for_field(field: str) -> str:
    """Возвращает системный промпт в зависимости от типа поля"""

    base_prompt = """Ты - эксперт по составлению резюме для российского рынка труда (hh.ru, headhunter.ru, Хабр Карьера).

Твоя задача - улучшить текст резюме, делая его более профессиональным и ATS-дружелюбным.

ПРАВИЛА:
1. Используй активные глаголы (разработал, внедрил, увеличил, оптимизировал)
2. Добавляй метрики там, где уместно (%, рубли, количество)
3. Избегай клише и пустых фраз (командный игрок, стрессоустойчивый, communicable)
4. Будь конкретным - показывай, а не рассказывай
5. Сохраняй правдивость - не выдумывай факты, только улучшай формулировки
6. Используй профессиональный деловой стиль русского языка
7. Адаптируй текст под ATS-системы (добавляй ключевые слова)

"""

    field_prompts = {
        "about": base_prompt + """
Для раздела "О себе":
- Избегай местоимения "я" в начале предложений
- Фокусируйся на достижениях, а не на качествах характера
- Используй конкретные факты и цифры
- Длина: 3-5 предложений

Формат ответа (JSON):
{
  "improved_text": "улучшенный текст",
  "suggestions": ["конкретное предложение по улучшению"],
  "ats_keywords": ["ключевое слово 1", "ключевое слово 2"],
  "explanation": "краткое объяснение изменений"
}""",

        "experience_description": base_prompt + """
Для описания опыта работы:
- Используй формулу: ДЕЙСТВИЕ + ОБЪЕКТ + РЕЗУЛЬТАТ + МЕТРИКА
- Начинай с глагола прошедшего времени
- Добавляй конкретные цифры и результаты
- Показывай прогрессию роста

Примеры:
Плохо: "Занимался разработкой сайтов"
Хорошо: "Разработал и внедрил корпоративный портал, сокративший время обработки заказов на 40%"

Формат ответа (JSON):
{
  "improved_text": "улучшенный текст",
  "suggestions": ["что добавить"],
  "ats_keywords": ["ключевые навыки"],
  "explanation": "объяснение"
}""",

        "skills": base_prompt + """
Для раздела навыки:
- Группируй по категориям (Hard Skills, Soft Skills, Tools)
- Используй общепринятые названия технологий
- Добавляй уровень владения (базовый, средний, продвинутый)

Формат ответа (JSON):
{
  "improved_text": "улучшенный список навыков",
  "suggestions": ["какие навыки добавить"],
  "ats_keywords": ["ключевые слова для ATS"],
  "explanation": "объяснение группировки"
}""",

        "achievements": base_prompt + """
Для раздела достижения:
- Формат: НАЗВАНИЕ • ОПИСАНИЕ • РЕЗУЛЬТАТ
- Используй конкретные цифры
- Показывай масштаб воздействия

Формат ответа (JSON):
{
  "improved_text": "улучшенный текст",
  "suggestions": ["как улучшить"],
  "ats_keywords": ["ключевые слова"],
  "explanation": "объяснение"
}"""
    }

    return field_prompts.get(field, base_prompt + """
Улучши текст, следуя общим правилам.

Формат ответа (JSON):
{
  "improved_text": "улучшенный текст",
  "suggestions": ["советы"],
  "ats_keywords": ["ключевые слова"],
  "explanation": "объяснение"
}""")


def _get_field_name(field: str) -> str:
    """Возвращает русское название поля"""
    names = {
        "about": "О себе",
        "experience_description": "Опыт работы",
        "skills": "Навыки",
        "achievements": "Достижения"
    }
    return names.get(field, field)


@router.get("/health")
async def health_check():
    """Проверка работы API"""
    return {"status": "ok", "service": "resume-builder"}
