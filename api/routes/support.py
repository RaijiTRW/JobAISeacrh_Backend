"""
Support Chat Routes - AI чат и поддержка
"""
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
import httpx

from config import get_settings
from services.auth_service import auth_service
from services.support_chat_service import support_chat_service
from services.admin_service import admin_service

router = APIRouter(prefix="/api/support", tags=["support"])
settings = get_settings()


class ChatMessageRequest(BaseModel):
    message: str
    chat_id: Optional[str] = None  # Для support chat


class AdminMessageRequest(BaseModel):
    chat_id: str
    message: str


class RatingRequest(BaseModel):
    chat_id: str
    rating: int


# === Вспомогательные функции ===

async def get_user_id(authorization: str) -> str:
    """Получить user_id из токена"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.replace("Bearer ", "")
    user_id = await auth_service.verify_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


async def require_admin(authorization: str) -> str:
    """Проверить что пользователь - админ"""
    user_id = await get_user_id(authorization)
    is_admin = await admin_service.is_admin(user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user_id


async def call_ai(message: str, context: str = "") -> str:
    """Вызов AI модели для ответа"""
    try:
        system_prompt = """Ты дружелюбный помощник на сайте поиска работы JobAISearch.
Отвечай кратко и по делу на русском языке.
Помогай пользователям с вопросами о поиске работы, резюме и функциях сайта.

Основные функции сайта:
- AI-поиск вакансий через чат
- Лента вакансий с фильтрами
- Создание резюме в профиле
- Чат с работодателями
- Подписка Pro дает 10 запросов в день (триал - 3 запроса)

Если пользователь хочет связаться с администрацией, скажи что его запрос отправлен и администратор ответит в ближайшее время."""

        if context:
            system_prompt += f"\n\nКонтекст предыдущих сообщений:\n{context}"

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
                        {"role": "user", "content": message},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                print(f"[Support AI] Error: {response.status_code} - {response.text}")
                return "Извините, произошла ошибка. Попробуйте позже."
    except Exception as e:
        print(f"[Support AI] Exception: {e}")
        return "Извините, произошла ошибка. Попробуйте позже."


# === Public endpoints ===

@router.get("/quick-questions")
async def get_quick_questions():
    """Получить список быстрых вопросов"""
    questions = await support_chat_service.get_quick_questions()
    return {"questions": [q.model_dump() for q in questions]}


@router.post("/ai-chat")
async def ai_chat(request: ChatMessageRequest, authorization: str = Header(None)):
    """Отправить сообщение в AI чат (без тикета поддержки)"""
    await get_user_id(authorization)

    # Простой AI ответ
    ai_response = await call_ai(request.message)
    return {"response": ai_response}


@router.post("/contact-admin")
async def contact_admin(authorization: str = Header(None)):
    """Создать тикет поддержки (связь с администрацией)"""
    user_id = await get_user_id(authorization)

    # Проверяем, нет ли уже активного чата
    existing_chat = await support_chat_service.get_user_active_chat(user_id)
    if existing_chat:
        return {
            "chat_id": existing_chat.id,
            "message": "У вас уже есть активный чат с поддержкой",
            "is_existing": True,
        }

    # Создаем новый чат
    chat_id = await support_chat_service.create_support_chat(user_id)
    if not chat_id:
        raise HTTPException(status_code=500, detail="Failed to create support chat")

    # Добавляем AI сообщение о том что запрос отправлен
    await support_chat_service.add_message(
        chat_id=chat_id,
        sender_type="ai",
        content="Ваш запрос отправлен администрации. Пожалуйста, опишите ваш вопрос, и администратор ответит вам в ближайшее время.",
    )

    return {
        "chat_id": chat_id,
        "message": "Чат с поддержкой создан",
        "is_existing": False,
    }


@router.get("/my-chat")
async def get_my_chat(authorization: str = Header(None)):
    """Получить активный чат пользователя"""
    user_id = await get_user_id(authorization)

    chat = await support_chat_service.get_user_active_chat(user_id)
    if not chat:
        return {"chat": None, "messages": []}

    messages = await support_chat_service.get_chat_messages(chat.id)

    # Отмечаем сообщения от админа как прочитанные
    await support_chat_service.mark_messages_read(chat.id, "user")

    return {
        "chat": chat.model_dump(),
        "messages": [m.model_dump() for m in messages],
    }


@router.post("/send-message")
async def send_message(request: ChatMessageRequest, authorization: str = Header(None)):
    """Отправить сообщение в чат поддержки"""
    user_id = await get_user_id(authorization)

    if not request.chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")

    # Проверяем что чат принадлежит пользователю
    chat = await support_chat_service.get_chat_by_id(request.chat_id)
    if not chat or chat["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    if chat["status"] != "active":
        raise HTTPException(status_code=400, detail="Chat is closed")

    # Добавляем сообщение пользователя
    message = await support_chat_service.add_message(
        chat_id=request.chat_id,
        sender_type="user",
        sender_id=user_id,
        content=request.message,
    )

    if not message:
        raise HTTPException(status_code=500, detail="Failed to send message")

    return {"message": message.model_dump()}


@router.post("/rate")
async def rate_chat(request: RatingRequest, authorization: str = Header(None)):
    """Оценить закрытый чат"""
    user_id = await get_user_id(authorization)

    # Проверяем что чат принадлежит пользователю
    chat = await support_chat_service.get_chat_by_id(request.chat_id)
    if not chat or chat["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    if chat["status"] != "closed":
        raise HTTPException(status_code=400, detail="Can only rate closed chats")

    if request.rating < 1 or request.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be 1-5")

    success = await support_chat_service.rate_chat(request.chat_id, request.rating)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to rate chat")

    return {"success": True}


# === Admin endpoints ===

@router.get("/admin/chats")
async def admin_get_chats(authorization: str = Header(None)):
    """Получить все активные чаты (админ)"""
    await require_admin(authorization)

    active_chats = await support_chat_service.get_active_chats()
    return {"chats": active_chats}


@router.get("/admin/archive")
async def admin_get_archive(authorization: str = Header(None)):
    """Получить архивные чаты (админ)"""
    await require_admin(authorization)

    archived_chats = await support_chat_service.get_archived_chats()
    return {"chats": archived_chats}


@router.get("/admin/chat/{chat_id}")
async def admin_get_chat(chat_id: str, authorization: str = Header(None)):
    """Получить конкретный чат с сообщениями (админ)"""
    await require_admin(authorization)

    chat = await support_chat_service.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = await support_chat_service.get_chat_messages(chat_id)

    # Отмечаем сообщения от пользователя как прочитанные
    await support_chat_service.mark_messages_read(chat_id, "admin")

    return {
        "chat": chat,
        "messages": [m.model_dump() for m in messages],
    }


@router.post("/admin/send-message")
async def admin_send_message(request: AdminMessageRequest, authorization: str = Header(None)):
    """Отправить сообщение от админа"""
    admin_id = await require_admin(authorization)

    chat = await support_chat_service.get_chat_by_id(request.chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if chat["status"] != "active":
        raise HTTPException(status_code=400, detail="Chat is closed")

    # Добавляем сообщение админа
    message = await support_chat_service.add_message(
        chat_id=request.chat_id,
        sender_type="admin",
        sender_id=admin_id,
        content=request.message,
    )

    if not message:
        raise HTTPException(status_code=500, detail="Failed to send message")

    return {"message": message.model_dump()}


@router.post("/admin/close-chat/{chat_id}")
async def admin_close_chat(chat_id: str, authorization: str = Header(None)):
    """Закрыть чат (админ)"""
    admin_id = await require_admin(authorization)

    chat = await support_chat_service.get_chat_by_id(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if chat["status"] != "active":
        raise HTTPException(status_code=400, detail="Chat already closed")

    # Добавляем AI сообщение о закрытии
    await support_chat_service.add_message(
        chat_id=chat_id,
        sender_type="ai",
        content="Чат завершён администратором. Спасибо за обращение! Пожалуйста, оцените качество поддержки (от 1 до 5 звёзд).",
    )

    # Закрываем чат
    success = await support_chat_service.close_chat(chat_id, admin_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to close chat")

    return {"success": True}
