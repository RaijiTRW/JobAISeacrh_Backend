"""
API эндпоинты для чата
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json
import asyncio
from datetime import datetime


def json_serializer(obj):
    """Сериализатор для datetime и других объектов"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

from models.chat import ChatRequest, ChatResponse, UserPreferences
from agents.orchestrator import orchestrator
from services.user_profile import user_profile_service
from services.subscription_service import subscription_service
from config import get_settings

router = APIRouter(prefix="/chat", tags=["chat"])

# Временное хранилище сессий (в проде использовать Redis)
sessions: dict[str, dict] = {}


class ChatMessageRequest(BaseModel):
    message: str
    chat_id: Optional[str] = None
    user_id: str
    search_in_feed: bool = True  # Поиск в ленте (БД)
    search_online: bool = True   # Поиск в сети (live)


class ChatMessageResponse(BaseModel):
    message: str
    vacancies: list[dict] = []
    chat_id: str


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: ChatMessageRequest):
    """
    Отправка сообщения в чат
    """
    # Проверяем лимит запросов
    can_use, limit_message = await subscription_service.check_and_use_request(request.user_id)
    if not can_use:
        raise HTTPException(status_code=402, detail=limit_message)

    # Проверяем доступ к поиску в сети (Base план не имеет доступа)
    status = await subscription_service.get_full_status(request.user_id)
    if status.subscription and not status.subscription.can_search_online:
        request.search_online = False  # Принудительно отключаем для Base плана

    # Получаем или создаём сессию
    session_key = f"{request.user_id}_{request.chat_id or 'new'}"

    if session_key not in sessions:
        sessions[session_key] = {
            "history": [],
            "preferences": UserPreferences(),
        }

    session = sessions[session_key]

    # Получаем данные профиля пользователя
    user_data = await user_profile_service.get_user_data(request.user_id)

    # Извлекаем предпочтения из сообщения
    session["preferences"] = await orchestrator.extract_preferences(
        request.message,
        session["preferences"],
    )

    # Добавляем сообщение в историю
    session["history"].append({
        "role": "user",
        "content": request.message,
    })

    # Обрабатываем через оркестратор
    chat_request = ChatRequest(
        message=request.message,
        chat_id=request.chat_id,
        user_id=request.user_id,
    )

    response = await orchestrator.process_message(
        chat_request,
        session["history"],
        session["preferences"],
        user_data,
    )

    # Добавляем ответ в историю
    session["history"].append({
        "role": "assistant",
        "content": response.message,
    })

    return ChatMessageResponse(
        message=response.message,
        vacancies=response.vacancies,
        chat_id=response.chat_id or session_key,
    )


@router.post("/message/stream")
async def send_message_stream(request: ChatMessageRequest):
    """
    Отправка сообщения со стримингом ответа
    """
    # Проверяем лимит запросов
    can_use, limit_message = await subscription_service.check_and_use_request(request.user_id)
    if not can_use:
        raise HTTPException(status_code=402, detail=limit_message)

    # Проверяем доступ к поиску в сети (Base план не имеет доступа)
    status = await subscription_service.get_full_status(request.user_id)
    if status.subscription and not status.subscription.can_search_online:
        request.search_online = False  # Принудительно отключаем для Base плана

    session_key = f"{request.user_id}_{request.chat_id or 'new'}"

    if session_key not in sessions:
        sessions[session_key] = {
            "history": [],
            "preferences": UserPreferences(),
        }

    session = sessions[session_key]

    # Получаем данные профиля пользователя
    user_data = await user_profile_service.get_user_data(request.user_id)

    # Извлекаем предпочтения
    session["preferences"] = await orchestrator.extract_preferences(
        request.message,
        session["preferences"],
    )

    session["history"].append({
        "role": "user",
        "content": request.message,
    })

    async def generate():
        """Генератор для SSE"""
        chat_request = ChatRequest(
            message=request.message,
            chat_id=request.chat_id,
            user_id=request.user_id,
            search_in_feed=request.search_in_feed,
            search_online=request.search_online,
        )

        response = await orchestrator.process_message(
            chat_request,
            session["history"],
            session["preferences"],
            user_data,
        )

        # Стримим текст по словам
        words = response.message.split()
        for i, word in enumerate(words):
            yield f"data: {json.dumps({'type': 'text', 'content': word + ' '})}\n\n"
            await asyncio.sleep(0.03)  # Имитация печати

        # Отправляем вакансии
        print(f"Vacancies to send: {len(response.vacancies)}")
        if response.vacancies:
            print(f"First vacancy: {response.vacancies[0] if response.vacancies else 'none'}")
            yield f"data: {json.dumps({'type': 'vacancies', 'content': response.vacancies}, default=json_serializer)}\n\n"

        # Отправляем отсеянные вакансии
        print(f"Rejected vacancies to send: {len(response.rejected_vacancies)}")
        if response.rejected_vacancies:
            yield f"data: {json.dumps({'type': 'rejected_vacancies', 'content': response.rejected_vacancies}, default=json_serializer)}\n\n"

        # Завершение
        yield f"data: {json.dumps({'type': 'done', 'chat_id': response.chat_id})}\n\n"

        # Сохраняем в историю
        session["history"].append({
            "role": "assistant",
            "content": response.message,
        })

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
    session_key = f"{user_id}_{chat_id}"
    if session_key in sessions:
        del sessions[session_key]
    return {"status": "ok"}


@router.get("/preferences/{user_id}")
async def get_preferences(user_id: str, chat_id: Optional[str] = None):
    """Получение текущих предпочтений"""
    session_key = f"{user_id}_{chat_id or 'new'}"
    if session_key in sessions:
        return sessions[session_key]["preferences"].model_dump()
    return UserPreferences().model_dump()


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
        "- Pro Trial: 7 дней бесплатно при регистрации, 15 запросов/день, полный доступ\n"
        "- Base: бесплатный план навсегда, 3 запроса/день, поиск только в ленте\n"
        "- Pro: 799₽/мес, 15 запросов/день, полный поиск включая сеть\n\n"
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
