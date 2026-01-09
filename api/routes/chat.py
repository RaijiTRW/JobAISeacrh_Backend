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
from config import get_settings

router = APIRouter(prefix="/chat", tags=["chat"])

# Временное хранилище сессий (в проде использовать Redis)
sessions: dict[str, dict] = {}


class ChatMessageRequest(BaseModel):
    message: str
    chat_id: Optional[str] = None
    user_id: str


class ChatMessageResponse(BaseModel):
    message: str
    vacancies: list[dict] = []
    chat_id: str


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: ChatMessageRequest):
    """
    Отправка сообщения в чат
    """
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
