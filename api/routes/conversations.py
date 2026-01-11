"""
API роутер для чатов между соискателями и работодателями
"""
from fastapi import APIRouter, HTTPException, Header, Query
from typing import Optional
from pydantic import BaseModel

from services.conversation_service import conversation_service, Message, Conversation
from services.employer_vacancy_service import employer_vacancy_service
from services.auth_service import get_user_from_token

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    vacancy_id: str


class MessageCreate(BaseModel):
    content: str


class ConversationResponse(BaseModel):
    id: str
    vacancy_id: str
    applicant_id: str
    employer_id: str
    status: str
    last_message_at: Optional[str] = None
    applicant_unread_count: int = 0
    employer_unread_count: int = 0
    created_at: Optional[str] = None
    vacancy_title: Optional[str] = None
    vacancy_company: Optional[str] = None
    applicant_name: Optional[str] = None


class ConversationListResponse(BaseModel):
    conversations: list[dict]
    total: int


class MessagesListResponse(BaseModel):
    messages: list[Message]
    total: int


class UnreadCountResponse(BaseModel):
    count: int


def get_user_id_and_token(authorization: str) -> tuple[str, str]:
    """Извлечь user_id и token из заголовка Authorization"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    token = authorization.replace("Bearer ", "")
    user = get_user_from_token(token)

    if not user:
        raise HTTPException(status_code=401, detail="Недействительный токен")

    return user.get("sub") or user.get("id"), token


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    data: ConversationCreate,
    authorization: str = Header(...),
):
    """Создать или получить чат для вакансии"""
    user_id, token = get_user_id_and_token(authorization)

    # Получаем вакансию для определения работодателя
    vacancy = await employer_vacancy_service.get_by_id(data.vacancy_id, token)
    if not vacancy:
        raise HTTPException(status_code=404, detail="Вакансия не найдена")

    # Нельзя написать себе
    if vacancy.user_id == user_id:
        raise HTTPException(status_code=400, detail="Нельзя написать себе")

    conversation = await conversation_service.get_or_create_conversation(
        vacancy_id=data.vacancy_id,
        applicant_id=user_id,
        employer_id=vacancy.user_id,
        token=token,
    )

    if not conversation:
        raise HTTPException(status_code=500, detail="Не удалось создать чат")

    return ConversationResponse(
        id=conversation.id,
        vacancy_id=conversation.vacancy_id,
        applicant_id=conversation.applicant_id,
        employer_id=conversation.employer_id,
        status=conversation.status,
        last_message_at=conversation.last_message_at,
        applicant_unread_count=conversation.applicant_unread_count,
        employer_unread_count=conversation.employer_unread_count,
        created_at=conversation.created_at,
        vacancy_title=vacancy.title,
        vacancy_company=vacancy.company,
    )


@router.get("", response_model=ConversationListResponse)
async def get_my_conversations(
    authorization: str = Header(...),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    """Получить свои чаты"""
    user_id, token = get_user_id_and_token(authorization)

    offset = (page - 1) * limit
    conversations, total = await conversation_service.get_user_conversations(
        user_id=user_id,
        token=token,
        limit=limit,
        offset=offset,
    )

    return ConversationListResponse(conversations=conversations, total=total)


@router.get("/unread", response_model=UnreadCountResponse)
async def get_unread_count(authorization: str = Header(...)):
    """Получить количество непрочитанных сообщений"""
    user_id, token = get_user_id_and_token(authorization)

    count = await conversation_service.get_unread_count(user_id, token)
    return UnreadCountResponse(count=count)


@router.get("/{conversation_id}/messages", response_model=MessagesListResponse)
async def get_messages(
    conversation_id: str,
    authorization: str = Header(...),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    """Получить сообщения из чата"""
    user_id, token = get_user_id_and_token(authorization)

    offset = (page - 1) * limit
    messages, total = await conversation_service.get_conversation_messages(
        conversation_id=conversation_id,
        token=token,
        limit=limit,
        offset=offset,
    )

    # Отмечаем как прочитанные
    await conversation_service.mark_as_read(conversation_id, user_id, token)

    return MessagesListResponse(messages=messages, total=total)


@router.post("/{conversation_id}/messages", response_model=Message)
async def send_message(
    conversation_id: str,
    data: MessageCreate,
    authorization: str = Header(...),
):
    """Отправить сообщение"""
    user_id, token = get_user_id_and_token(authorization)

    if not data.content.strip():
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")

    message = await conversation_service.send_message(
        conversation_id=conversation_id,
        sender_id=user_id,
        content=data.content.strip(),
        token=token,
    )

    if not message:
        raise HTTPException(status_code=500, detail="Не удалось отправить сообщение")

    return message


@router.post("/{conversation_id}/read")
async def mark_as_read(
    conversation_id: str,
    authorization: str = Header(...),
):
    """Отметить сообщения как прочитанные"""
    user_id, token = get_user_id_and_token(authorization)

    success = await conversation_service.mark_as_read(conversation_id, user_id, token)
    if not success:
        raise HTTPException(status_code=500, detail="Не удалось отметить как прочитанное")

    return {"success": True}
