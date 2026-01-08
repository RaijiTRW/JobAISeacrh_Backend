"""
API эндпоинты для чата
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json
import asyncio

from app.models.chat import ChatRequest, ChatResponse, UserPreferences
from app.agents.orchestrator import orchestrator
from app.config import get_settings

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
        )

        # Стримим текст по словам
        words = response.message.split()
        for i, word in enumerate(words):
            yield f"data: {json.dumps({'type': 'text', 'content': word + ' '})}\n\n"
            await asyncio.sleep(0.03)  # Имитация печати

        # Отправляем вакансии
        if response.vacancies:
            yield f"data: {json.dumps({'type': 'vacancies', 'content': response.vacancies})}\n\n"

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
