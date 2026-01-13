"""
Support Chat Service - AI чат и поддержка пользователей
"""
import httpx
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from config import get_settings


class SupportChat(BaseModel):
    """Чат поддержки"""
    id: str
    user_id: str
    status: str
    admin_id: Optional[str] = None
    rating: Optional[int] = None
    created_at: str
    closed_at: Optional[str] = None
    # Дополнительные поля для отображения
    user_email: Optional[str] = None
    messages_count: int = 0
    last_message: Optional[str] = None


class SupportMessage(BaseModel):
    """Сообщение в чате поддержки"""
    id: str
    chat_id: str
    sender_type: str  # user, admin, ai
    sender_id: Optional[str] = None
    content: str
    is_read: bool = False
    created_at: str


class QuickQuestion(BaseModel):
    """Быстрый вопрос"""
    id: str
    label: str
    prompt: str
    icon: Optional[str] = None


class SupportChatService:
    """Сервис для чата поддержки"""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.supabase_url
        self.api_key = self.settings.supabase_key

    def _headers(self) -> dict:
        return {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def get_quick_questions(self) -> list[QuickQuestion]:
        """Получить список быстрых вопросов"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/chat_quick_questions",
                    params={
                        "select": "id,label,prompt,icon",
                        "is_active": "eq.true",
                        "order": "sort_order.asc",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )
                if response.status_code == 200:
                    return [QuickQuestion(**q) for q in response.json()]
            return []
        except Exception as e:
            print(f"[SupportChat] get_quick_questions error: {e}")
            return []

    async def create_support_chat(self, user_id: str) -> Optional[str]:
        """Создать новый чат поддержки"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/rest/v1/support_chats",
                    headers={**self._headers(), "Prefer": "return=representation"},
                    json={"user_id": user_id, "status": "active"},
                    timeout=10.0,
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    if data:
                        return data[0]["id"]
            return None
        except Exception as e:
            print(f"[SupportChat] create_support_chat error: {e}")
            return None

    async def get_user_active_chat(self, user_id: str) -> Optional[SupportChat]:
        """Получить активный чат пользователя"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/support_chats",
                    params={
                        "user_id": f"eq.{user_id}",
                        "status": "eq.active",
                        "select": "*",
                        "order": "created_at.desc",
                        "limit": "1",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    if data:
                        return SupportChat(**data[0])
            return None
        except Exception as e:
            print(f"[SupportChat] get_user_active_chat error: {e}")
            return None

    async def get_chat_messages(self, chat_id: str) -> list[SupportMessage]:
        """Получить сообщения чата"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/support_messages",
                    params={
                        "chat_id": f"eq.{chat_id}",
                        "select": "*",
                        "order": "created_at.asc",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )
                if response.status_code == 200:
                    return [SupportMessage(**m) for m in response.json()]
            return []
        except Exception as e:
            print(f"[SupportChat] get_chat_messages error: {e}")
            return []

    async def add_message(
        self,
        chat_id: str,
        sender_type: str,
        content: str,
        sender_id: Optional[str] = None,
    ) -> Optional[SupportMessage]:
        """Добавить сообщение в чат"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/rest/v1/support_messages",
                    headers={**self._headers(), "Prefer": "return=representation"},
                    json={
                        "chat_id": chat_id,
                        "sender_type": sender_type,
                        "sender_id": sender_id,
                        "content": content,
                    },
                    timeout=10.0,
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    if data:
                        return SupportMessage(**data[0])
            return None
        except Exception as e:
            print(f"[SupportChat] add_message error: {e}")
            return None

    async def mark_messages_read(self, chat_id: str, sender_type: str) -> bool:
        """Отметить сообщения как прочитанные"""
        try:
            # Отмечаем прочитанными сообщения от противоположной стороны
            read_type = "admin" if sender_type == "user" else "user"
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/support_messages",
                    params={
                        "chat_id": f"eq.{chat_id}",
                        "sender_type": f"eq.{read_type}",
                        "is_read": "eq.false",
                    },
                    headers=self._headers(),
                    json={"is_read": True},
                    timeout=10.0,
                )
                return response.status_code in (200, 204)
        except Exception as e:
            print(f"[SupportChat] mark_messages_read error: {e}")
            return False

    # === Admin методы ===

    async def get_active_chats(self) -> list[dict]:
        """Получить все активные чаты для админки"""
        try:
            async with httpx.AsyncClient() as client:
                # Получаем чаты
                response = await client.get(
                    f"{self.base_url}/rest/v1/support_chats",
                    params={
                        "status": "eq.active",
                        "select": "*",
                        "order": "created_at.desc",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )
                if response.status_code != 200:
                    return []

                chats = response.json()
                result = []

                for chat in chats:
                    # Получаем email пользователя
                    profile_resp = await client.get(
                        f"{self.base_url}/rest/v1/profiles",
                        params={
                            "user_id": f"eq.{chat['user_id']}",
                            "select": "email",
                        },
                        headers=self._headers(),
                        timeout=10.0,
                    )
                    user_email = None
                    if profile_resp.status_code == 200:
                        profiles = profile_resp.json()
                        if profiles:
                            user_email = profiles[0].get("email")

                    # Получаем последнее сообщение и количество
                    msg_resp = await client.get(
                        f"{self.base_url}/rest/v1/support_messages",
                        params={
                            "chat_id": f"eq.{chat['id']}",
                            "select": "content,sender_type",
                            "order": "created_at.desc",
                            "limit": "1",
                        },
                        headers=self._headers(),
                        timeout=10.0,
                    )
                    last_message = None
                    if msg_resp.status_code == 200:
                        msgs = msg_resp.json()
                        if msgs:
                            last_message = msgs[0].get("content", "")[:100]

                    # Количество непрочитанных от пользователя
                    unread_resp = await client.get(
                        f"{self.base_url}/rest/v1/support_messages",
                        params={
                            "chat_id": f"eq.{chat['id']}",
                            "sender_type": "eq.user",
                            "is_read": "eq.false",
                            "select": "id",
                        },
                        headers={**self._headers(), "Prefer": "count=exact"},
                        timeout=10.0,
                    )
                    unread_count = 0
                    if unread_resp.status_code in (200, 206):
                        content_range = unread_resp.headers.get("content-range", "")
                        if "/" in content_range:
                            unread_count = int(content_range.split("/")[1])

                    result.append({
                        **chat,
                        "user_email": user_email,
                        "last_message": last_message,
                        "unread_count": unread_count,
                    })

                return result
        except Exception as e:
            print(f"[SupportChat] get_active_chats error: {e}")
            return []

    async def get_archived_chats(self, limit: int = 50) -> list[dict]:
        """Получить архивные чаты"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/support_chats",
                    params={
                        "status": "eq.closed",
                        "select": "*",
                        "order": "closed_at.desc",
                        "limit": str(limit),
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )
                if response.status_code != 200:
                    return []

                chats = response.json()
                result = []

                for chat in chats:
                    # Получаем email пользователя
                    profile_resp = await client.get(
                        f"{self.base_url}/rest/v1/profiles",
                        params={
                            "user_id": f"eq.{chat['user_id']}",
                            "select": "email",
                        },
                        headers=self._headers(),
                        timeout=10.0,
                    )
                    user_email = None
                    if profile_resp.status_code == 200:
                        profiles = profile_resp.json()
                        if profiles:
                            user_email = profiles[0].get("email")

                    result.append({
                        **chat,
                        "user_email": user_email,
                    })

                return result
        except Exception as e:
            print(f"[SupportChat] get_archived_chats error: {e}")
            return []

    async def close_chat(self, chat_id: str, admin_id: str) -> bool:
        """Закрыть чат"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/support_chats",
                    params={"id": f"eq.{chat_id}"},
                    headers=self._headers(),
                    json={
                        "status": "closed",
                        "admin_id": admin_id,
                        "closed_at": datetime.utcnow().isoformat(),
                    },
                    timeout=10.0,
                )
                return response.status_code in (200, 204)
        except Exception as e:
            print(f"[SupportChat] close_chat error: {e}")
            return False

    async def rate_chat(self, chat_id: str, rating: int) -> bool:
        """Оценить чат"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/support_chats",
                    params={"id": f"eq.{chat_id}"},
                    headers=self._headers(),
                    json={
                        "rating": rating,
                        "feedback_submitted_at": datetime.utcnow().isoformat(),
                    },
                    timeout=10.0,
                )
                return response.status_code in (200, 204)
        except Exception as e:
            print(f"[SupportChat] rate_chat error: {e}")
            return False

    async def get_chat_by_id(self, chat_id: str) -> Optional[dict]:
        """Получить чат по ID"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/support_chats",
                    params={"id": f"eq.{chat_id}", "select": "*"},
                    headers=self._headers(),
                    timeout=10.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    if data:
                        return data[0]
            return None
        except Exception as e:
            print(f"[SupportChat] get_chat_by_id error: {e}")
            return None


# Singleton
support_chat_service = SupportChatService()
