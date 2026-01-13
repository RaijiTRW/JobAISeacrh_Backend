"""
API эндпоинты для подписок и платежей
"""
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel
from typing import Optional

from services.subscription_service import subscription_service, SubscriptionStatus
from services.yookassa_service import yookassa_service
from services.auth_service import get_user_from_token
from config import get_settings


router = APIRouter(prefix="/api/subscription", tags=["subscription"])
settings = get_settings()


# ============ Request/Response Models ============

class SubscriptionResponse(BaseModel):
    """Ответ с информацией о подписке"""
    subscription: Optional[dict] = None
    limits: dict
    is_trial_expired: bool
    is_pro: bool
    prices: dict  # Цены для UI


class CheckoutResponse(BaseModel):
    """Ответ при создании платежа"""
    payment_id: str
    payment_url: str


class CheckPaymentResponse(BaseModel):
    """Ответ проверки статуса платежа"""
    status: str
    paid: bool


# ============ Endpoints ============

@router.get("", response_model=SubscriptionResponse)
async def get_subscription(authorization: str = Header(...)):
    """
    Получить информацию о подписке текущего пользователя.
    Требует авторизации.
    """
    # Извлекаем user_id из токена
    user = await get_user_from_token(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Неверный токен")

    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID не найден")

    # Получаем статус подписки
    status = await subscription_service.get_full_status(user_id)

    # Формируем ответ
    subscription_data = None
    if status.subscription:
        subscription_data = {
            "plan": status.subscription.plan,
            "status": status.subscription.status,
            "expires_at": status.subscription.expires_at,
            "days_left": status.subscription.days_left,
        }

    return SubscriptionResponse(
        subscription=subscription_data,
        limits={
            "daily_limit": status.limits.daily_limit,
            "daily_used": status.limits.daily_used,
            "bonus_requests": status.limits.bonus_requests,
            "remaining": status.limits.remaining,
            "can_use": status.limits.can_use,
        },
        is_trial_expired=status.is_trial_expired,
        is_pro=status.is_pro,
        prices={
            "subscription": settings.subscription_price,
            "extra_requests": settings.extra_requests_price,
            "extra_requests_count": settings.extra_requests_count,
        },
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(authorization: str = Header(...)):
    """
    Создать платёж для подписки Pro.
    Возвращает URL для редиректа на страницу оплаты YooKassa.
    """
    # Проверяем авторизацию
    user = await get_user_from_token(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Неверный токен")

    user_id = user.get("id")
    email = user.get("email", "")

    if not user_id:
        raise HTTPException(status_code=401, detail="User ID не найден")

    # Проверяем, настроена ли YooKassa
    if not yookassa_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Платёжная система временно недоступна"
        )

    # Создаём платёж
    result = await yookassa_service.create_subscription_payment(
        user_id=user_id,
        email=email,
        description="Подписка Pro на 1 месяц — AI Working Search",
    )

    if not result:
        raise HTTPException(
            status_code=500,
            detail="Не удалось создать платёж. Попробуйте позже."
        )

    # Сохраняем информацию о платеже
    await subscription_service.save_payment(
        user_id=user_id,
        payment_type="subscription",
        amount=settings.subscription_price,
        status="pending",
        yookassa_payment_id=result["payment_id"],
        metadata={"plan": "pro"},
    )

    return CheckoutResponse(
        payment_id=result["payment_id"],
        payment_url=result["confirmation_url"],
    )


@router.post("/extra", response_model=CheckoutResponse)
async def buy_extra_requests(authorization: str = Header(...)):
    """
    Создать платёж для докупки запросов.
    """
    # Проверяем авторизацию
    user = await get_user_from_token(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Неверный токен")

    user_id = user.get("id")
    email = user.get("email", "")

    if not user_id:
        raise HTTPException(status_code=401, detail="User ID не найден")

    # Проверяем, что у пользователя есть активная подписка
    status = await subscription_service.get_full_status(user_id)
    if not status.is_pro:
        raise HTTPException(
            status_code=400,
            detail="Докупка запросов доступна только для Pro подписчиков"
        )

    # Проверяем, настроена ли YooKassa
    if not yookassa_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Платёжная система временно недоступна"
        )

    # Создаём платёж
    result = await yookassa_service.create_extra_requests_payment(
        user_id=user_id,
        email=email,
    )

    if not result:
        raise HTTPException(
            status_code=500,
            detail="Не удалось создать платёж. Попробуйте позже."
        )

    # Сохраняем информацию о платеже
    await subscription_service.save_payment(
        user_id=user_id,
        payment_type="extra_requests",
        amount=settings.extra_requests_price,
        status="pending",
        yookassa_payment_id=result["payment_id"],
        metadata={"count": settings.extra_requests_count},
    )

    return CheckoutResponse(
        payment_id=result["payment_id"],
        payment_url=result["confirmation_url"],
    )


@router.get("/check/{payment_id}", response_model=CheckPaymentResponse)
async def check_payment(payment_id: str, authorization: str = Header(...)):
    """
    Проверить статус платежа.
    Используется для polling после возврата с YooKassa.
    """
    # Проверяем авторизацию
    user = await get_user_from_token(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Неверный токен")

    # Проверяем статус в YooKassa
    result = await yookassa_service.check_payment_status(payment_id)

    if not result:
        raise HTTPException(status_code=404, detail="Платёж не найден")

    return CheckPaymentResponse(
        status=result["status"],
        paid=result["paid"],
    )


@router.post("/webhook")
async def yookassa_webhook(request: Request):
    """
    Webhook для уведомлений от YooKassa.
    Вызывается YooKassa при изменении статуса платежа.
    """
    try:
        body = await request.body()
        print(f"[Webhook] Received: {body.decode()}")

        # Парсим уведомление
        notification = yookassa_service.parse_webhook(body)

        if not notification:
            print("[Webhook] Failed to parse notification")
            return {"status": "error", "message": "Invalid notification"}

        print(f"[Webhook] Parsed: {notification}")

        payment_id = notification.get("payment_id")
        event = notification.get("event")
        status = notification.get("status")
        user_id = notification.get("user_id")
        payment_type = notification.get("type")

        # Обновляем статус платежа в БД
        if payment_id:
            await subscription_service.update_payment_status(
                yookassa_payment_id=payment_id,
                status=status,
                yookassa_status=event,
            )

        # Если платёж успешен — активируем подписку или добавляем запросы
        if status == "succeeded" and notification.get("paid"):
            if payment_type == "subscription" and user_id:
                print(f"[Webhook] Activating Pro for user {user_id}")
                success = await subscription_service.activate_pro(user_id, payment_id)
                if success:
                    print(f"[Webhook] Pro activated for user {user_id}")
                else:
                    print(f"[Webhook] Failed to activate Pro for user {user_id}")

            elif payment_type == "extra_requests" and user_id:
                count = notification.get("count", settings.extra_requests_count)
                print(f"[Webhook] Adding {count} bonus requests for user {user_id}")
                success = await subscription_service.add_bonus_requests(user_id, count)
                if success:
                    print(f"[Webhook] Bonus requests added for user {user_id}")
                else:
                    print(f"[Webhook] Failed to add bonus requests for user {user_id}")

        return {"status": "ok"}

    except Exception as e:
        print(f"[Webhook] Error: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/create-trial")
async def create_trial(authorization: str = Header(...)):
    """
    Создать триал подписку для пользователя.
    Вызывается если триггер в БД не сработал.
    """
    user = await get_user_from_token(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Неверный токен")

    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID не найден")

    # Проверяем, есть ли уже подписка
    existing = await subscription_service.get_subscription(user_id)
    if existing:
        return {"status": "exists", "message": "Подписка уже существует"}

    # Создаём триал
    success = await subscription_service.create_trial(user_id)

    if success:
        return {"status": "ok", "message": "Триал создан"}
    else:
        raise HTTPException(
            status_code=500,
            detail="Не удалось создать триал"
        )
