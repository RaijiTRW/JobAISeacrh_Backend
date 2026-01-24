"""
Сервис для чатов между соискателями и работодателями
"""
import httpx
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from config import get_settings


class Message(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    content: str
    is_read: bool = False
    created_at: Optional[str] = None


class Conversation(BaseModel):
    id: str
    vacancy_id: str
    applicant_id: str
    employer_id: str
    status: str = "active"
    last_message_at: Optional[str] = None
    applicant_unread_count: int = 0
    employer_unread_count: int = 0
    created_at: Optional[str] = None
    # Дополнительные поля для отображения
    vacancy_title: Optional[str] = None
    other_user_name: Optional[str] = None
    last_message: Optional[str] = None


class ConversationService:
    """Сервис для работы с чатами"""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.supabase_url
        self.api_key = self.settings.supabase_service_key or self.settings.supabase_key

    def _headers(self, token: Optional[str] = None) -> dict:
        auth_token = token or self.api_key
        return {
            "apikey": self.api_key,
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

    async def get_or_create_conversation(
        self,
        vacancy_id: str,
        applicant_id: str,
        employer_id: str,
        token: str,
    ) -> Optional[Conversation]:
        """Получить или создать чат для вакансии"""
        try:
            async with httpx.AsyncClient() as client:
                # Проверяем существующий чат
                response = await client.get(
                    f"{self.base_url}/rest/v1/conversations",
                    params={
                        "vacancy_id": f"eq.{vacancy_id}",
                        "applicant_id": f"eq.{applicant_id}",
                        "select": "*",
                    },
                    headers=self._headers(token),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data:
                        return Conversation(**data[0])

                # Создаём новый чат
                response = await client.post(
                    f"{self.base_url}/rest/v1/conversations",
                    headers={**self._headers(token), "Prefer": "return=representation"},
                    json={
                        "vacancy_id": vacancy_id,
                        "applicant_id": applicant_id,
                        "employer_id": employer_id,
                    },
                    timeout=10.0,
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    if data:
                        return Conversation(**data[0])

            return None
        except Exception as e:
            print(f"[Conversation] get_or_create error: {e}")
            return None

    async def get_user_conversations(
        self,
        user_id: str,
        token: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Получить все чаты пользователя"""
        try:
            async with httpx.AsyncClient() as client:
                # Получаем чаты где пользователь — соискатель или работодатель
                response = await client.get(
                    f"{self.base_url}/rest/v1/conversations",
                    params={
                        "or": f"(applicant_id.eq.{user_id},employer_id.eq.{user_id})",
                        "select": "*,employer_vacancies(title,company),profiles!conversations_applicant_id_fkey(full_name,email)",
                        "order": "last_message_at.desc.nullsfirst",
                        "limit": str(limit),
                        "offset": str(offset),
                    },
                    headers={**self._headers(token), "Prefer": "count=exact"},
                    timeout=10.0,
                )

                if response.status_code in (200, 206):
                    data = response.json()
                    total = 0
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        total = int(content_range.split("/")[1])

                    # Обогащаем данные
                    conversations = []
                    for row in data:
                        conv = {
                            "id": row["id"],
                            "vacancy_id": row["vacancy_id"],
                            "applicant_id": row["applicant_id"],
                            "employer_id": row["employer_id"],
                            "status": row["status"],
                            "last_message_at": row.get("last_message_at"),
                            "applicant_unread_count": row.get("applicant_unread_count", 0),
                            "employer_unread_count": row.get("employer_unread_count", 0),
                            "created_at": row.get("created_at"),
                        }

                        # Добавляем информацию о вакансии
                        if row.get("employer_vacancies"):
                            conv["vacancy_title"] = row["employer_vacancies"].get("title")
                            conv["vacancy_company"] = row["employer_vacancies"].get("company")

                        # Определяем имя собеседника
                        if row.get("profiles"):
                            conv["applicant_name"] = row["profiles"].get("full_name") or row["profiles"].get("email")

                        conversations.append(conv)

                    return conversations, total

            return [], 0
        except Exception as e:
            print(f"[Conversation] get_user_conversations error: {e}")
            return [], 0

    async def get_conversation_messages(
        self,
        conversation_id: str,
        token: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Message], int]:
        """Получить сообщения из чата"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/conversation_messages",
                    params={
                        "conversation_id": f"eq.{conversation_id}",
                        "select": "*",
                        "order": "created_at.desc",
                        "limit": str(limit),
                        "offset": str(offset),
                    },
                    headers={**self._headers(token), "Prefer": "count=exact"},
                    timeout=10.0,
                )

                if response.status_code in (200, 206):
                    data = response.json()
                    total = 0
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        total = int(content_range.split("/")[1])

                    messages = [Message(**row) for row in data]
                    return messages, total

            return [], 0
        except Exception as e:
            print(f"[Conversation] get_messages error: {e}")
            return [], 0

    async def send_message(
        self,
        conversation_id: str,
        sender_id: str,
        content: str,
        token: str,
    ) -> Optional[Message]:
        """Отправить сообщение"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/rest/v1/conversation_messages",
                    headers={**self._headers(token), "Prefer": "return=representation"},
                    json={
                        "conversation_id": conversation_id,
                        "sender_id": sender_id,
                        "content": content,
                    },
                    timeout=10.0,
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    if data:
                        return Message(**data[0])

            return None
        except Exception as e:
            print(f"[Conversation] send_message error: {e}")
            return None

    async def mark_as_read(
        self,
        conversation_id: str,
        user_id: str,
        token: str,
    ) -> bool:
        """Отметить сообщения как прочитанные"""
        try:
            async with httpx.AsyncClient() as client:
                # Сначала получаем информацию о чате
                conv_response = await client.get(
                    f"{self.base_url}/rest/v1/conversations",
                    params={
                        "id": f"eq.{conversation_id}",
                        "select": "applicant_id,employer_id",
                    },
                    headers=self._headers(token),
                    timeout=10.0,
                )

                if conv_response.status_code != 200:
                    return False

                conv_data = conv_response.json()
                if not conv_data:
                    return False

                conv = conv_data[0]
                is_applicant = conv["applicant_id"] == user_id

                # Обновляем счётчик непрочитанных
                update_field = "applicant_unread_count" if is_applicant else "employer_unread_count"
                response = await client.patch(
                    f"{self.base_url}/rest/v1/conversations",
                    params={"id": f"eq.{conversation_id}"},
                    headers=self._headers(token),
                    json={update_field: 0},
                    timeout=10.0,
                )

                if response.status_code not in (200, 204):
                    return False

                # Отмечаем сообщения как прочитанные
                other_user_id = conv["employer_id"] if is_applicant else conv["applicant_id"]
                response = await client.patch(
                    f"{self.base_url}/rest/v1/conversation_messages",
                    params={
                        "conversation_id": f"eq.{conversation_id}",
                        "sender_id": f"eq.{other_user_id}",
                        "is_read": "eq.false",
                    },
                    headers=self._headers(token),
                    json={"is_read": True},
                    timeout=10.0,
                )

                return response.status_code in (200, 204)
        except Exception as e:
            print(f"[Conversation] mark_as_read error: {e}")
            return False

    async def get_unread_count(self, user_id: str, token: str) -> int:
        """Получить общее количество непрочитанных сообщений"""
        try:
            async with httpx.AsyncClient() as client:
                # Суммируем непрочитанные как соискатель
                response1 = await client.get(
                    f"{self.base_url}/rest/v1/conversations",
                    params={
                        "applicant_id": f"eq.{user_id}",
                        "select": "applicant_unread_count",
                    },
                    headers=self._headers(token),
                    timeout=10.0,
                )

                # Суммируем непрочитанные как работодатель
                response2 = await client.get(
                    f"{self.base_url}/rest/v1/conversations",
                    params={
                        "employer_id": f"eq.{user_id}",
                        "select": "employer_unread_count",
                    },
                    headers=self._headers(token),
                    timeout=10.0,
                )

                total = 0
                if response1.status_code == 200:
                    for row in response1.json():
                        total += row.get("applicant_unread_count", 0)

                if response2.status_code == 200:
                    for row in response2.json():
                        total += row.get("employer_unread_count", 0)

                return total
        except Exception as e:
            print(f"[Conversation] get_unread_count error: {e}")
            return 0


# Singleton
conversation_service = ConversationService()
