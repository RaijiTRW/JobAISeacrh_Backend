"""
Subscription Service - управление подписками и лимитами запросов
"""
import httpx
from datetime import datetime, timedelta, date
from typing import Optional
from pydantic import BaseModel

from config import get_settings


class SubscriptionInfo(BaseModel):
    """Информация о подписке"""
    plan: str  # pro_trial, base, pro
    status: str  # active, expired, cancelled
    expires_at: Optional[str] = None  # None для base плана (навсегда)
    days_left: Optional[int] = None  # None для base плана
    started_at: Optional[str] = None
    can_search_online: bool = True  # False для base плана


class RequestLimits(BaseModel):
    """Лимиты запросов пользователя"""
    daily_limit: int
    daily_used: int
    bonus_requests: int
    can_use: bool
    remaining: int  # daily_limit - daily_used + bonus_requests


class SubscriptionStatus(BaseModel):
    """Полный статус подписки пользователя"""
    subscription: Optional[SubscriptionInfo] = None
    limits: RequestLimits
    # Флаги планов
    is_pro_trial: bool = False  # На Pro Trial (7 дней)
    is_base: bool = False  # На Base (бесплатный навсегда)
    is_pro: bool = False  # На Pro (платная подписка)
    is_pro_trial_expired: bool = False  # Pro Trial истёк, показать модалку


class SubscriptionService:
    """Сервис для управления подписками и лимитами"""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.supabase_url
        self.api_key = self.settings.supabase_key

    def _headers(self) -> dict:
        return {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    async def get_subscription(self, user_id: str) -> Optional[SubscriptionInfo]:
        """Получить информацию о подписке пользователя"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/user_subscriptions",
                    params={
                        "user_id": f"eq.{user_id}",
                        "select": "*",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data and len(data) > 0:
                        sub = data[0]

                        # Для base плана expires_at может быть NULL
                        expires_at_str = sub.get("expires_at")
                        days_left = None
                        if expires_at_str:
                            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                            now = datetime.now(expires_at.tzinfo)
                            days_left = max(0, (expires_at - now).days)

                        return SubscriptionInfo(
                            plan=sub["plan"],
                            status=sub["status"],
                            expires_at=expires_at_str,
                            days_left=days_left,
                            started_at=sub.get("started_at"),
                            can_search_online=sub.get("can_search_online", True),
                        )
                return None
        except Exception as e:
            print(f"[Subscription] get_subscription error: {e}")
            return None

    async def get_limits(self, user_id: str) -> RequestLimits:
        """Получить лимиты запросов пользователя"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/user_request_limits",
                    params={
                        "user_id": f"eq.{user_id}",
                        "select": "*",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data and len(data) > 0:
                        limits = data[0]
                        daily_limit = limits["daily_limit"]
                        daily_used = limits["daily_used"]
                        bonus = limits["bonus_requests"]

                        # Проверяем, нужно ли сбросить дневные лимиты
                        reset_date = date.fromisoformat(limits["daily_reset_at"])
                        if reset_date < date.today():
                            # Сбрасываем лимиты
                            await self._reset_user_daily_limits(user_id)
                            daily_used = 0

                        remaining = max(0, daily_limit - daily_used) + bonus
                        can_use = remaining > 0

                        return RequestLimits(
                            daily_limit=daily_limit,
                            daily_used=daily_used,
                            bonus_requests=bonus,
                            can_use=can_use,
                            remaining=remaining,
                        )

            # Дефолтные лимиты если записи нет
            return RequestLimits(
                daily_limit=0,
                daily_used=0,
                bonus_requests=0,
                can_use=False,
                remaining=0,
            )
        except Exception as e:
            print(f"[Subscription] get_limits error: {e}")
            return RequestLimits(
                daily_limit=0,
                daily_used=0,
                bonus_requests=0,
                can_use=False,
                remaining=0,
            )

    async def get_full_status(self, user_id: str) -> SubscriptionStatus:
        """Получить полный статус подписки и лимитов"""
        subscription = await self.get_subscription(user_id)
        limits = await self.get_limits(user_id)

        is_pro_trial = False
        is_base = False
        is_pro = False
        is_pro_trial_expired = False

        if subscription:
            plan = subscription.plan
            status = subscription.status

            # Определяем текущий план
            is_pro_trial = plan == "pro_trial" and status == "active"
            is_base = plan == "base"
            is_pro = plan == "pro" and status == "active"

            # Pro Trial истёк = сейчас на base (после истечения pro_trial)
            # Показываем модалку только при переходе на base
            is_pro_trial_expired = is_base

        return SubscriptionStatus(
            subscription=subscription,
            limits=limits,
            is_pro_trial=is_pro_trial,
            is_base=is_base,
            is_pro=is_pro,
            is_pro_trial_expired=is_pro_trial_expired,
        )

    async def check_and_use_request(self, user_id: str) -> tuple[bool, str]:
        """
        Проверить лимит и использовать запрос.
        Возвращает (can_use, message)
        """
        try:
            # Получаем статус
            status = await self.get_full_status(user_id)

            if not status.subscription:
                return False, "Подписка не найдена. Пожалуйста, зарегистрируйтесь."

            plan = status.subscription.plan
            sub_status = status.subscription.status

            # Pro подписка истекла
            if plan == "pro" and sub_status == "expired":
                return False, "Подписка Pro истекла. Продлите подписку."

            if sub_status == "cancelled":
                return False, "Подписка отменена. Оформите новую подписку."

            # Все активные планы (pro_trial, base, pro) могут использовать запросы
            # Просто проверяем лимиты
            if not status.limits.can_use:
                if status.is_base:
                    return False, "Лимит запросов исчерпан. Оформите Pro для большего количества запросов."
                else:
                    return False, "Лимит запросов исчерпан. Докупите запросы или подождите до завтра."

            # Используем запрос
            await self._use_request(user_id, status.limits)
            return True, "OK"

        except Exception as e:
            print(f"[Subscription] check_and_use_request error: {e}")
            return False, f"Ошибка проверки лимитов: {str(e)}"

    async def _use_request(self, user_id: str, limits: RequestLimits) -> None:
        """Использовать один запрос"""
        try:
            async with httpx.AsyncClient() as client:
                # Сначала используем дневные лимиты
                if limits.daily_used < limits.daily_limit:
                    await client.patch(
                        f"{self.base_url}/rest/v1/user_request_limits",
                        params={"user_id": f"eq.{user_id}"},
                        json={
                            "daily_used": limits.daily_used + 1,
                            "updated_at": datetime.utcnow().isoformat(),
                        },
                        headers=self._headers(),
                        timeout=10.0,
                    )
                # Иначе используем бонусные
                elif limits.bonus_requests > 0:
                    await client.patch(
                        f"{self.base_url}/rest/v1/user_request_limits",
                        params={"user_id": f"eq.{user_id}"},
                        json={
                            "bonus_requests": limits.bonus_requests - 1,
                            "updated_at": datetime.utcnow().isoformat(),
                        },
                        headers=self._headers(),
                        timeout=10.0,
                    )
        except Exception as e:
            print(f"[Subscription] _use_request error: {e}")

    async def _reset_user_daily_limits(self, user_id: str) -> None:
        """Сбросить дневные лимиты пользователя"""
        try:
            async with httpx.AsyncClient() as client:
                await client.patch(
                    f"{self.base_url}/rest/v1/user_request_limits",
                    params={"user_id": f"eq.{user_id}"},
                    json={
                        "daily_used": 0,
                        "daily_reset_at": date.today().isoformat(),
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )
        except Exception as e:
            print(f"[Subscription] _reset_user_daily_limits error: {e}")

    async def activate_pro(self, user_id: str, payment_id: str) -> bool:
        """Активировать Pro подписку после оплаты"""
        try:
            async with httpx.AsyncClient() as client:
                now = datetime.utcnow()
                expires_at = now + timedelta(days=30)

                # Обновляем подписку
                response = await client.patch(
                    f"{self.base_url}/rest/v1/user_subscriptions",
                    params={"user_id": f"eq.{user_id}"},
                    json={
                        "plan": "pro",
                        "status": "active",
                        "started_at": now.isoformat(),
                        "expires_at": expires_at.isoformat(),
                        "yookassa_payment_id": payment_id,
                        "can_search_online": True,  # Pro имеет доступ к поиску в сети
                        "updated_at": now.isoformat(),
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code not in [200, 204]:
                    print(f"[Subscription] activate_pro subscription update failed: {response.text}")
                    return False

                # Обновляем лимиты
                response = await client.patch(
                    f"{self.base_url}/rest/v1/user_request_limits",
                    params={"user_id": f"eq.{user_id}"},
                    json={
                        "daily_limit": self.settings.pro_daily_limit,
                        "daily_used": 0,
                        "daily_reset_at": date.today().isoformat(),
                        "updated_at": now.isoformat(),
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                return response.status_code in [200, 204]

        except Exception as e:
            print(f"[Subscription] activate_pro error: {e}")
            return False

    async def add_bonus_requests(self, user_id: str, count: int) -> bool:
        """Добавить бонусные запросы после покупки"""
        try:
            # Сначала получаем текущие бонусы
            limits = await self.get_limits(user_id)

            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/rest/v1/user_request_limits",
                    params={"user_id": f"eq.{user_id}"},
                    json={
                        "bonus_requests": limits.bonus_requests + count,
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                return response.status_code in [200, 204]

        except Exception as e:
            print(f"[Subscription] add_bonus_requests error: {e}")
            return False

    async def create_pro_trial(self, user_id: str) -> bool:
        """Создать Pro Trial подписку для нового пользователя (7 дней)"""
        try:
            async with httpx.AsyncClient() as client:
                now = datetime.utcnow()
                expires_at = now + timedelta(days=self.settings.pro_trial_days)

                # Создаём подписку Pro Trial
                response = await client.post(
                    f"{self.base_url}/rest/v1/user_subscriptions",
                    json={
                        "user_id": user_id,
                        "plan": "pro_trial",
                        "status": "active",
                        "started_at": now.isoformat(),
                        "expires_at": expires_at.isoformat(),
                        "can_search_online": True,  # Pro Trial имеет полный доступ
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code not in [200, 201]:
                    print(f"[Subscription] create_pro_trial subscription failed: {response.text}")
                    return False

                # Создаём лимиты (15 запросов/день как у Pro)
                response = await client.post(
                    f"{self.base_url}/rest/v1/user_request_limits",
                    json={
                        "user_id": user_id,
                        "daily_limit": self.settings.pro_trial_daily_limit,
                        "daily_used": 0,
                        "daily_reset_at": date.today().isoformat(),
                        "bonus_requests": 0,
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                return response.status_code in [200, 201]

        except Exception as e:
            print(f"[Subscription] create_pro_trial error: {e}")
            return False

    async def downgrade_to_base(self, user_id: str) -> bool:
        """Перевести пользователя на Base план (после истечения Pro Trial)"""
        try:
            async with httpx.AsyncClient() as client:
                now = datetime.utcnow()

                # Обновляем подписку на Base
                response = await client.patch(
                    f"{self.base_url}/rest/v1/user_subscriptions",
                    params={"user_id": f"eq.{user_id}"},
                    json={
                        "plan": "base",
                        "status": "active",
                        "expires_at": None,  # Base навсегда
                        "can_search_online": False,  # Только лента
                        "updated_at": now.isoformat(),
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code not in [200, 204]:
                    print(f"[Subscription] downgrade_to_base failed: {response.text}")
                    return False

                # Обновляем лимиты на Base (3 запроса/день)
                response = await client.patch(
                    f"{self.base_url}/rest/v1/user_request_limits",
                    params={"user_id": f"eq.{user_id}"},
                    json={
                        "daily_limit": self.settings.base_daily_limit,
                        "updated_at": now.isoformat(),
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                return response.status_code in [200, 204]

        except Exception as e:
            print(f"[Subscription] downgrade_to_base error: {e}")
            return False

    async def save_payment(
        self,
        user_id: str,
        payment_type: str,
        amount: float,
        status: str,
        yookassa_payment_id: str,
        metadata: dict = None,
    ) -> bool:
        """Сохранить информацию о платеже"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/rest/v1/payment_history",
                    json={
                        "user_id": user_id,
                        "type": payment_type,
                        "amount": amount,
                        "currency": "RUB",
                        "status": status,
                        "yookassa_payment_id": yookassa_payment_id,
                        "metadata": metadata or {},
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                return response.status_code in [200, 201]

        except Exception as e:
            print(f"[Subscription] save_payment error: {e}")
            return False

    async def update_payment_status(
        self,
        yookassa_payment_id: str,
        status: str,
        yookassa_status: str = None,
    ) -> bool:
        """Обновить статус платежа"""
        try:
            async with httpx.AsyncClient() as client:
                data = {"status": status}
                if yookassa_status:
                    data["yookassa_status"] = yookassa_status

                response = await client.patch(
                    f"{self.base_url}/rest/v1/payment_history",
                    params={"yookassa_payment_id": f"eq.{yookassa_payment_id}"},
                    json=data,
                    headers=self._headers(),
                    timeout=10.0,
                )

                return response.status_code in [200, 204]

        except Exception as e:
            print(f"[Subscription] update_payment_status error: {e}")
            return False

    async def get_payment_by_id(self, yookassa_payment_id: str) -> Optional[dict]:
        """Получить платёж по ID YooKassa"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/payment_history",
                    params={
                        "yookassa_payment_id": f"eq.{yookassa_payment_id}",
                        "select": "*",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data and len(data) > 0:
                        return data[0]
                return None

        except Exception as e:
            print(f"[Subscription] get_payment_by_id error: {e}")
            return None


# Синглтон
subscription_service = SubscriptionService()
