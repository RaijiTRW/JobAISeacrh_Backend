"""
Admin API Routes
"""
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from services.admin_service import admin_service, UserProfile
from services.auth_service import get_user_from_token


router = APIRouter(prefix="/api/admin", tags=["admin"])


# === Models ===

class BanRequest(BaseModel):
    reason: Optional[str] = None


class SubscriptionRequest(BaseModel):
    subscription_type: Optional[str] = None
    expires_at: Optional[str] = None


class UsersListResponse(BaseModel):
    users: list[UserProfile]
    total: int
    page: int
    pages: int


class ToggleVacanciesRequest(BaseModel):
    can_create: bool


class RoleRequest(BaseModel):
    role: str


class StatsResponse(BaseModel):
    total_users: int
    online_users: int
    banned_users: int
    admins_count: int
    platform_vacancies: int


# === Helpers ===

async def require_admin(authorization: str) -> str:
    """Проверить, что пользователь — админ"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")

    token = authorization.replace("Bearer ", "")
    user = get_user_from_token(token)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user")

    is_admin = await admin_service.is_admin(user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    return user_id


# === Routes ===

@router.get("/stats", response_model=StatsResponse)
async def get_admin_stats(authorization: str = Header(None)):
    """Получить статистику для админки"""
    await require_admin(authorization)

    online_users = await admin_service.get_online_count()
    _, total_users = await admin_service.get_all_users(limit=1)
    banned_users = await admin_service.get_banned_count()
    admins_count = await admin_service.get_admins_count()
    platform_vacancies = await admin_service.get_platform_vacancies_count()

    return StatsResponse(
        total_users=total_users,
        online_users=online_users,
        banned_users=banned_users,
        admins_count=admins_count,
        platform_vacancies=platform_vacancies,
    )


@router.get("/users", response_model=UsersListResponse)
async def get_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    authorization: str = Header(None),
):
    """Получить список пользователей"""
    await require_admin(authorization)

    offset = (page - 1) * limit
    users, total = await admin_service.get_all_users(
        limit=limit, offset=offset, search=search
    )

    pages = (total + limit - 1) // limit if total > 0 else 1

    return UsersListResponse(users=users, total=total, page=page, pages=pages)


@router.post("/users/{user_id}/ban")
async def ban_user(
    user_id: str,
    request: BanRequest,
    authorization: str = Header(None),
):
    """Забанить пользователя"""
    await require_admin(authorization)

    success = await admin_service.ban_user(user_id, request.reason)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to ban user")

    return {"success": True, "message": "User banned"}


@router.post("/users/{user_id}/unban")
async def unban_user(user_id: str, authorization: str = Header(None)):
    """Разбанить пользователя"""
    await require_admin(authorization)

    success = await admin_service.unban_user(user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to unban user")

    return {"success": True, "message": "User unbanned"}


@router.post("/users/{user_id}/toggle-vacancies")
async def toggle_vacancy_creation(
    user_id: str,
    request: ToggleVacanciesRequest,
    authorization: str = Header(None),
):
    """Включить/выключить создание вакансий для пользователя"""
    await require_admin(authorization)

    success = await admin_service.set_can_create_vacancies(user_id, request.can_create)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update user")

    return {"success": True, "can_create_vacancies": request.can_create}


@router.post("/users/{user_id}/subscription")
async def set_user_subscription(
    user_id: str,
    request: SubscriptionRequest,
    authorization: str = Header(None),
):
    """Установить подписку пользователю"""
    await require_admin(authorization)

    success = await admin_service.set_subscription(
        user_id, request.subscription_type, request.expires_at
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update subscription")

    return {"success": True, "subscription_type": request.subscription_type}


@router.post("/users/{user_id}/role")
async def set_user_role(
    user_id: str,
    request: RoleRequest,
    authorization: str = Header(None),
):
    """Установить роль пользователю"""
    admin_id = await require_admin(authorization)

    if request.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role")

    # Нельзя убрать админку у себя
    if user_id == admin_id and request.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot remove your own admin role")

    success = await admin_service.set_role(user_id, request.role)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update role")

    return {"success": True, "role": request.role}


# === Site Settings ===

class SiteSettingItem(BaseModel):
    id: str
    value: dict
    updated_at: Optional[str] = None


@router.get("/settings")
async def get_site_settings(authorization: str = Header(None)) -> list[SiteSettingItem]:
    """Получить настройки сайта"""
    await require_admin(authorization)

    settings = await admin_service.get_site_settings_list()
    return settings


class SettingValueRequest(BaseModel):
    value: dict


@router.put("/settings/{setting_id}")
async def update_site_setting(
    setting_id: str,
    request: SettingValueRequest,
    authorization: str = Header(None),
):
    """Обновить настройку сайта"""
    await require_admin(authorization)

    valid_settings = [
        "registration_enabled",
        "chat_enabled",
        "vacancies_enabled",
        "vacancy_creation_enabled",
    ]
    if setting_id not in valid_settings:
        raise HTTPException(status_code=400, detail="Invalid setting")

    success = await admin_service.update_site_setting(setting_id, request.value)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update setting")

    return {"success": True, "setting": setting_id, "value": request.value}


# === Public endpoint for checking settings ===

@router.get("/public/settings")
async def get_public_settings():
    """Получить публичные настройки (для проверки доступа)"""
    settings = await admin_service.get_site_settings()

    return {
        "registration_enabled": settings.get("registration_enabled", {}).get("enabled", True),
        "chat_enabled": settings.get("chat_enabled", {}).get("enabled", True),
        "vacancies_enabled": settings.get("vacancies_enabled", {}).get("enabled", True),
    }
