"""
Admin Service - управление пользователями и настройками сайта
"""
import httpx
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel

from config import get_settings


class UserProfile(BaseModel):
    """Профиль пользователя для админки"""
    id: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: str = "user"
    is_banned: bool = False
    ban_reason: Optional[str] = None
    can_create_vacancies: bool = True
    subscription_type: Optional[str] = None
    subscription_expires_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    created_at: Optional[str] = None


class SiteSetting(BaseModel):
    """Настройка сайта"""
    id: str
    value: dict
    updated_at: Optional[str] = None


class AdminService:
    """Сервис для админ-операций"""

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

    async def is_admin(self, user_id: str) -> bool:
        """Проверить, является ли пользователь админом"""
        try:
            print(f"[Admin] is_admin check for user_id: {user_id}")
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/profiles",
                    params={"user_id": f"eq.{user_id}", "select": "role"},
                    headers=self._headers(),
                    timeout=10.0,
                )
                print(f"[Admin] is_admin response status: {response.status_code}")
                print(f"[Admin] is_admin response body: {response.text}")
                if response.status_code == 200:
                    data = response.json()
                    if data and len(data) > 0:
                        role = data[0].get("role")
                        print(f"[Admin] Found role: {role}")
                        return role == "admin"
                    else:
                        print(f"[Admin] No profile found for user_id: {user_id}")
            return False
        except Exception as e:
            print(f"[Admin] is_admin error: {e}")
            return False

    async def get_all_users(
        self,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
    ) -> tuple[list[UserProfile], int]:
        """Получить список всех пользователей"""
        try:
            params = {
                "select": "*",
                "order": "created_at.desc",
                "limit": str(limit),
                "offset": str(offset),
            }

            if search:
                params["or"] = f"(email.ilike.%{search}%,full_name.ilike.%{search}%)"

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/profiles",
                    params=params,
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )

                if response.status_code in (200, 206):
                    data = response.json()
                    total = 0
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        total = int(content_range.split("/")[1])

                    users = [UserProfile(**row) for row in data]
                    return users, total

            return [], 0
        except Exception as e:
            print(f"[Admin] get_all_users error: {e}")
            return [], 0

    async def get_online_count(self, minutes: int = 5) -> int:
        """Получить количество онлайн пользователей"""
        try:
            threshold = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/profiles",
                    params={
                        "select": "id",
                        "last_seen_at": f"gte.{threshold}",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )

                if response.status_code in (200, 206):
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        return int(content_range.split("/")[1])
            return 0
        except Exception as e:
            print(f"[Admin] get_online_count error: {e}")
            return 0

    async def get_banned_count(self) -> int:
        """Получить количество забаненных пользователей"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/profiles",
                    params={
                        "select": "id",
                        "is_banned": "eq.true",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )

                if response.status_code in (200, 206):
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        return int(content_range.split("/")[1])
            return 0
        except Exception as e:
            print(f"[Admin] get_banned_count error: {e}")
            return 0

    async def get_admins_count(self) -> int:
        """Получить количество админов"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/profiles",
                    params={
                        "select": "id",
                        "role": "eq.admin",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )

                if response.status_code in (200, 206):
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        return int(content_range.split("/")[1])
            return 0
        except Exception as e:
            print(f"[Admin] get_admins_count error: {e}")
            return 0

    async def get_platform_vacancies_count(self) -> int:
        """Получить количество вакансий платформы"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/employer_vacancies",
                    params={
                        "select": "id",
                        "status": "eq.published",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )

                if response.status_code in (200, 206):
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        return int(content_range.split("/")[1])
            return 0
        except Exception as e:
            print(f"[Admin] get_platform_vacancies_count error: {e}")
            return 0

    async def ban_user(self, user_id: str, reason: Optional[str] = None) -> bool:
        """Забанить пользователя"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/profiles",
                    params={"user_id": f"eq.{user_id}"},
                    headers=self._headers(),
                    json={"is_banned": True, "ban_reason": reason},
                    timeout=10.0,
                )
                return response.status_code in (200, 204)
        except Exception as e:
            print(f"[Admin] ban_user error: {e}")
            return False

    async def unban_user(self, user_id: str) -> bool:
        """Разбанить пользователя"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/profiles",
                    params={"user_id": f"eq.{user_id}"},
                    headers=self._headers(),
                    json={"is_banned": False, "ban_reason": None},
                    timeout=10.0,
                )
                return response.status_code in (200, 204)
        except Exception as e:
            print(f"[Admin] unban_user error: {e}")
            return False

    async def set_can_create_vacancies(self, user_id: str, can_create: bool) -> bool:
        """Установить право создания вакансий"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/profiles",
                    params={"user_id": f"eq.{user_id}"},
                    headers=self._headers(),
                    json={"can_create_vacancies": can_create},
                    timeout=10.0,
                )
                return response.status_code in (200, 204)
        except Exception as e:
            print(f"[Admin] set_can_create_vacancies error: {e}")
            return False

    async def set_subscription(
        self,
        user_id: str,
        subscription_type: Optional[str],
        expires_at: Optional[str] = None,
    ) -> bool:
        """Установить подписку пользователю"""
        try:
            async with httpx.AsyncClient() as client:
                # Определяем параметры подписки
                if subscription_type == "trial":
                    daily_limit = 3
                    days = 3
                elif subscription_type == "pro":
                    daily_limit = 10
                    days = 30
                else:
                    # Удалить подписку
                    daily_limit = 0
                    days = 0

                if subscription_type:
                    # Вычисляем дату окончания
                    calc_expires_at = expires_at or (datetime.utcnow() + timedelta(days=days)).isoformat()

                    # Upsert в user_subscriptions
                    sub_response = await client.post(
                        f"{self.base_url}/rest/v1/user_subscriptions",
                        headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                        json={
                            "user_id": user_id,
                            "plan": subscription_type,
                            "status": "active",
                            "started_at": datetime.utcnow().isoformat(),
                            "expires_at": calc_expires_at,
                            "updated_at": datetime.utcnow().isoformat(),
                        },
                        timeout=10.0,
                    )
                    print(f"[Admin] user_subscriptions upsert: {sub_response.status_code}")

                    # Upsert в user_request_limits
                    limits_response = await client.post(
                        f"{self.base_url}/rest/v1/user_request_limits",
                        headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                        json={
                            "user_id": user_id,
                            "daily_limit": daily_limit,
                            "daily_used": 0,
                            "daily_reset_at": datetime.utcnow().strftime("%Y-%m-%d"),
                            "bonus_requests": 0,
                            "updated_at": datetime.utcnow().isoformat(),
                        },
                        timeout=10.0,
                    )
                    print(f"[Admin] user_request_limits upsert: {limits_response.status_code}")

                    # Также обновляем profiles для отображения в админке
                    await client.patch(
                        f"{self.base_url}/rest/v1/profiles",
                        params={"user_id": f"eq.{user_id}"},
                        headers=self._headers(),
                        json={
                            "subscription_type": subscription_type,
                            "subscription_expires_at": calc_expires_at,
                        },
                        timeout=10.0,
                    )

                    return sub_response.status_code in (200, 201, 204) and limits_response.status_code in (200, 201, 204)
                else:
                    # Удалить подписку
                    await client.delete(
                        f"{self.base_url}/rest/v1/user_subscriptions",
                        params={"user_id": f"eq.{user_id}"},
                        headers=self._headers(),
                        timeout=10.0,
                    )
                    await client.delete(
                        f"{self.base_url}/rest/v1/user_request_limits",
                        params={"user_id": f"eq.{user_id}"},
                        headers=self._headers(),
                        timeout=10.0,
                    )
                    # Очищаем поле в profiles
                    await client.patch(
                        f"{self.base_url}/rest/v1/profiles",
                        params={"user_id": f"eq.{user_id}"},
                        headers=self._headers(),
                        json={
                            "subscription_type": None,
                            "subscription_expires_at": None,
                        },
                        timeout=10.0,
                    )
                    return True
        except Exception as e:
            print(f"[Admin] set_subscription error: {e}")
            return False

    async def set_role(self, user_id: str, role: str) -> bool:
        """Установить роль пользователю"""
        if role not in ("user", "admin"):
            return False
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/profiles",
                    params={"user_id": f"eq.{user_id}"},
                    headers=self._headers(),
                    json={"role": role},
                    timeout=10.0,
                )
                return response.status_code in (200, 204)
        except Exception as e:
            print(f"[Admin] set_role error: {e}")
            return False

    # === Site Settings ===

    async def get_site_settings(self) -> dict[str, dict]:
        """Получить все настройки сайта как словарь"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/site_settings",
                    params={"select": "*"},
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    return {row["id"]: row["value"] for row in data}
            return {}
        except Exception as e:
            print(f"[Admin] get_site_settings error: {e}")
            return {}

    async def get_site_settings_list(self) -> list[dict]:
        """Получить настройки сайта как список"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/site_settings",
                    params={"select": "*"},
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    return response.json()
            return []
        except Exception as e:
            print(f"[Admin] get_site_settings_list error: {e}")
            return []

    async def update_site_setting(self, setting_id: str, value: dict) -> bool:
        """Обновить настройку сайта"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/site_settings",
                    params={"id": f"eq.{setting_id}"},
                    headers=self._headers(),
                    json={"value": value, "updated_at": datetime.utcnow().isoformat()},
                    timeout=10.0,
                )
                return response.status_code in (200, 204)
        except Exception as e:
            print(f"[Admin] update_site_setting error: {e}")
            return False

    async def is_feature_enabled(self, feature: str) -> bool:
        """Проверить, включена ли функция"""
        settings = await self.get_site_settings()
        setting = settings.get(feature, {})
        return setting.get("enabled", True)

    async def update_last_seen(self, user_id: str) -> bool:
        """Обновить last_seen пользователя"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/profiles",
                    params={"user_id": f"eq.{user_id}"},
                    headers=self._headers(),
                    json={"last_seen_at": datetime.utcnow().isoformat()},
                    timeout=5.0,
                )
                return response.status_code in (200, 204)
        except Exception:
            return False


# Singleton
admin_service = AdminService()
