"""
API эндпоинты для чата
Архитектура CrewAI: интерактивный AI-партнер по поиску работы
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional
import json
import asyncio
from datetime import datetime


def json_serializer(obj):
    """Сериализатор для datetime и других объектов"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


from models.chat import LifestylePreferences
from agents.crewai import JobSearchCrew, session_manager
from services.user_profile import user_profile_service
from services.subscription_service import subscription_service
from config import get_settings
from agents.agents_config import track_usage

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessageRequest(BaseModel):
    message: str
    chat_id: Optional[str] = None
    user_id: str
    search_in_feed: bool = True  # Поиск в ленте (БД)
    search_online: bool = True   # Поиск в сети (live)
    exclude_vacancy_ids: list[str] = Field(default_factory=list)  # ID вакансий для исключения
    lifestyle_preferences: Optional[LifestylePreferences] = None


class ChatMessageResponse(BaseModel):
    message: str
    vacancies: list[dict] = Field(default_factory=list)
    rejected_vacancies: list[dict] = Field(default_factory=list)
    chat_id: str


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: ChatMessageRequest):
    """
    Отправка сообщения в чат.
    CrewAI: интерактивный AI-партнер, задаёт уточняющие вопросы при необходимости.
    """
    # Проверяем лимит запросов
    can_use, limit_message = await subscription_service.check_and_use_request(request.user_id)
    if not can_use:
        raise HTTPException(status_code=402, detail=limit_message)

    # Проверяем доступ к поиску в сети (Base план не имеет доступа)
    status = await subscription_service.get_full_status(request.user_id)
    use_live_search = request.search_online
    if status.subscription and not status.subscription.can_search_online:
        use_live_search = False  # Принудительно отключаем для Base плана
    use_feed_search = request.search_in_feed
    if not use_feed_search and not use_live_search:
        use_feed_search = True

    # Получаем или создаём сессию
    session = session_manager.get_or_create_session(
        request.user_id,
        request.chat_id
    )

    # Получаем данные профиля пользователя
    user_data = await user_profile_service.get_user_data(request.user_id)
    user_data_dict = user_data.model_dump(mode='json') if user_data else {}

    print(f"[Chat] Processing message: {request.message[:50]}...")

    # Запускаем CrewAI
    crew = JobSearchCrew(
        user_id=request.user_id,
        user_data=user_data_dict,
        session=session,
    )

    result = await crew.process_message(
        message=request.message,
        conversation_history=session.get_recent_history(),
        use_live_search=use_live_search,
        use_feed_search=use_feed_search,
        exclude_vacancy_ids=request.exclude_vacancy_ids,
        lifestyle_preferences=request.lifestyle_preferences.model_dump() if request.lifestyle_preferences else None,
    )

    # Сохраняем в историю
    session.add_message("user", request.message)
    session.add_message("assistant", result.response_text)

    print(
        f"[Chat] Response type: {result.request_type}, "
        f"vacancies: {len(result.vacancies)}, rejected: {len(result.rejected_vacancies)}"
    )

    # Конвертируем вакансии в JSON
    vacancies_json = []
    for v in result.vacancies:
        if hasattr(v, 'model_dump'):
            vacancies_json.append(v.model_dump(mode='json'))
        elif isinstance(v, dict):
            vacancies_json.append(v)

    rejected_vacancies_json = []
    for v in result.rejected_vacancies:
        if hasattr(v, 'model_dump'):
            rejected_vacancies_json.append(v.model_dump(mode='json'))
        elif isinstance(v, dict):
            rejected_vacancies_json.append(v)

    return ChatMessageResponse(
        message=result.response_text,
        vacancies=vacancies_json,
        rejected_vacancies=rejected_vacancies_json,
        chat_id=session.id,
    )


@router.post("/message/stream")
async def send_message_stream(request: ChatMessageRequest):
    """
    Отправка сообщения со стримингом ответа.
    CrewAI с progressive loading вакансий.
    """
    # Проверяем лимит запросов
    can_use, limit_message = await subscription_service.check_and_use_request(request.user_id)
    if not can_use:
        raise HTTPException(status_code=402, detail=limit_message)

    # Проверяем доступ к поиску в сети
    status = await subscription_service.get_full_status(request.user_id)
    use_live_search = request.search_online
    if status.subscription and not status.subscription.can_search_online:
        use_live_search = False
    use_feed_search = request.search_in_feed
    if not use_feed_search and not use_live_search:
        use_feed_search = True

    # Получаем или создаём сессию
    session = session_manager.get_or_create_session(
        request.user_id,
        request.chat_id
    )

    # Получаем данные профиля
    user_data = await user_profile_service.get_user_data(request.user_id)
    user_data_dict = user_data.model_dump(mode='json') if user_data else {}

    session.add_message("user", request.message)

    async def generate():
        """Генератор для SSE с CrewAI - streaming вакансий по мере нахождения"""

        # Отправляем начальный статус
        yield f"data: {json.dumps({'type': 'progress', 'message': 'starting'})}\n\n"

        # Запускаем CrewAI
        crew = JobSearchCrew(
            user_id=request.user_id,
            user_data=user_data_dict,
            session=session,
        )

        try:
            # Используем streaming variant
            response_text_accumulated = ""

            async for event in crew.process_message_stream(
                message=request.message,
                conversation_history=session.get_recent_history(limit=10),
                use_live_search=use_live_search,
                use_feed_search=use_feed_search,
                exclude_vacancy_ids=request.exclude_vacancy_ids,
                lifestyle_preferences=request.lifestyle_preferences.model_dump() if request.lifestyle_preferences else None,
            ):
                if event['type'] == 'text':
                    # Стримим текст по словам
                    words = event['content'].split()
                    for word in words:
                        yield f"data: {json.dumps({'type': 'text', 'content': word + ' '})}\n\n"
                        await asyncio.sleep(0.02)
                    response_text_accumulated = event['content']

                elif event['type'] == 'vacancies_chunk':
                    # Отправляем чанк вакансий сразу
                    vacancies_json = []
                    for v in event['content']:
                        if hasattr(v, 'model_dump'):
                            vacancies_json.append(v.model_dump(mode='json'))
                        elif isinstance(v, dict):
                            vacancies_json.append(v)

                    print(f"[Chat Stream] Sending vacancies_chunk with {len(vacancies_json)} vacancies")
                    yield f"data: {json.dumps({'type': 'vacancies_chunk', 'content': vacancies_json}, default=json_serializer)}\n\n"
                    print(f"[Chat Stream] vacancies_chunk sent successfully")

                elif event['type'] == 'rejected_vacancies':
                    rejected_json = []
                    for v in event['content']:
                        if hasattr(v, 'model_dump'):
                            rejected_json.append(v.model_dump(mode='json'))
                        elif isinstance(v, dict):
                            rejected_json.append(v)
                    yield f"data: {json.dumps({'type': 'rejected_vacancies', 'content': rejected_json}, default=json_serializer)}\n\n"

                elif event['type'] == 'progress':
                    # Прогресс можно логировать, но не отправляем клиенту
                    print(f"[Chat Stream] Progress: {event.get('message', '')}")

                elif event['type'] == 'done':
                    # Сохраняем в историю
                    if response_text_accumulated:
                        session.add_message("assistant", response_text_accumulated)
                    yield f"data: {json.dumps({'type': 'done', 'chat_id': session.id})}\n\n"

        except Exception as e:
            print(f"[Chat Stream] Error: {e}")
            import traceback
            traceback.print_exc()

            error_message = "Произошла ошибка при обработке запроса. Попробуй ещё раз."
            yield f"data: {json.dumps({'type': 'text', 'content': error_message})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'chat_id': session.id})}\n\n"

            session.add_message("assistant", error_message)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str, user_id: str):
    """Удаление чата"""
    session_manager.delete_session(user_id, chat_id)
    return {"status": "ok"}


@router.get("/preferences/{user_id}")
async def get_preferences(user_id: str, chat_id: Optional[str] = None):
    """Получение текущих предпочтений из сессии"""
    session = session_manager.get_session(user_id, chat_id)
    if session:
        return session.preferences.model_dump()
    return {"city": None, "professions": [], "salary_from": None}


@router.get("/history/{user_id}")
async def get_history(user_id: str, chat_id: Optional[str] = None, limit: int = 20):
    """Получение истории чата"""
    session = session_manager.get_session(user_id, chat_id)
    if session:
        return {"history": session.get_recent_history(limit=limit)}
    return {"history": []}


# === Support Chat (FloatingChat) ===

class SupportChatRequest(BaseModel):
    message: str
    context: Optional[str] = None


class SupportChatResponse(BaseModel):
    response: str
    connect_to_admin: bool = False


# Ключевые слова для определения запроса к администратору
ADMIN_KEYWORDS = [
    "человек", "живой", "оператор", "администратор", "админ", "поддержка",
    "связаться", "позвонить", "менеджер", "консультант", "помощь живого",
    "реальный человек", "не бот", "хочу поговорить", "нужна помощь человека",
    "соединить с", "переключить на", "написать админу", "жалоба",
    "проблема с оплатой", "не работает оплата", "возврат денег", "отменить подписку",
]

import re

ADMIN_PATTERNS = [
    r"свяжи(те)?.*с.*человек",
    r"хочу.*говорить.*с.*человек",
    r"нужен.*живой",
    r"перевед(и|ите).*на.*оператор",
    r"можно.*поговорить.*с",
    r"есть.*живой.*оператор",
    r"как.*связаться.*с.*поддержк",
    r"хочу.*пожаловаться",
    r"вернуть.*деньги",
]


def detect_admin_request(message: str) -> bool:
    """Определяет, хочет ли пользователь связаться с админом"""
    lower_message = message.lower()

    for keyword in ADMIN_KEYWORDS:
        if keyword in lower_message:
            return True

    for pattern in ADMIN_PATTERNS:
        if re.search(pattern, lower_message, re.IGNORECASE):
            return True

    return False


@router.post("/support", response_model=SupportChatResponse)
async def support_chat(request: SupportChatRequest):
    """
    AI-чат поддержки для FloatingChat (не поиск вакансий)
    """
    import httpx

    settings = get_settings()
    needs_admin = detect_admin_request(request.message)

    system_prompt = (
        "Ты дружелюбный помощник на сайте поиска работы JobAISearch.\n"
        "Пользователь хочет связаться с живым администратором.\n"
        "Скажи что понимаешь его и сейчас подключишь к администратору.\n"
        "Будь вежлив и краток. Ответь на русском языке."
        if needs_admin else
        "Ты дружелюбный помощник на сайте поиска работы JobAISearch.\n"
        "Отвечай кратко и по делу на русском языке.\n"
        "Помогай пользователям с вопросами о поиске работы, резюме и функциях сайта.\n\n"
        "Основные функции сайта:\n"
        "- AI-поиск вакансий через чат\n"
        "- Лента вакансий с фильтрами\n"
        "- Создание резюме в профиле\n"
        "- Чат с работодателями\n"
        "- Pro Trial: 3 дня бесплатно при регистрации, 15 запросов/день, полный доступ\n"
        "- Base: бесплатный план навсегда, 3 запроса/день, поиск только в ленте\n"
        "- Pro: 499₽/мес, 15 запросов/день, полный поиск включая сеть\n\n"
        "Если не можешь помочь с вопросом, предложи связаться с администратором."
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": request.message},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                # Трекинг токенов для support chat
                usage = data.get("usage", {})
                if usage:
                    try:
                        track_usage(
                            "support_chat",
                            settings.model_name,
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0),
                        )
                    except Exception:
                        pass
                ai_response = data["choices"][0]["message"]["content"]
                return SupportChatResponse(response=ai_response, connect_to_admin=needs_admin)
            else:
                print(f"[Support Chat] Error: {response.status_code} {response.text}")
                return SupportChatResponse(
                    response="Подключаю вас к администратору..." if needs_admin else "Извините, произошла ошибка. Попробуйте позже.",
                    connect_to_admin=needs_admin
                )
    except Exception as e:
        print(f"[Support Chat] Exception: {e}")
        return SupportChatResponse(
            response="Подключаю вас к администратору..." if needs_admin else "Извините, произошла ошибка. Попробуйте позже.",
            connect_to_admin=needs_admin
        )
