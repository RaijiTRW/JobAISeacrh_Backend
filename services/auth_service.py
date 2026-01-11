"""
Сервис авторизации - работа с JWT токенами Supabase
"""
import jwt
from typing import Optional
from config import get_settings


def get_user_from_token(token: str) -> Optional[dict]:
    """
    Декодировать JWT токен Supabase и получить данные пользователя.
    Возвращает dict с полями: sub (user_id), email, и др.
    """
    try:
        # Supabase использует JWT, декодируем без верификации подписи
        # (подпись проверяется на уровне Supabase при запросах к БД)
        payload = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["HS256"]
        )
        return payload

    except jwt.ExpiredSignatureError:
        print("[Auth] Token expired")
        return None
    except jwt.InvalidTokenError as e:
        print(f"[Auth] Invalid token: {e}")
        return None
    except Exception as e:
        print(f"[Auth] Token decode error: {e}")
        return None


def get_user_id_from_token(token: str) -> Optional[str]:
    """Получить только user_id из токена"""
    user = get_user_from_token(token)
    if user:
        return user.get("sub")
    return None
