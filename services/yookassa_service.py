"""
YooKassa Service - интеграция с платёжной системой YooKassa
https://yookassa.ru/developers/api
"""
import uuid
import hmac
import hashlib
import json
from typing import Optional
from datetime import datetime

from config import get_settings

# YooKassa SDK
try:
    from yookassa import Configuration, Payment
    from yookassa.domain.notification import WebhookNotificationEventType, WebhookNotification
    YOOKASSA_AVAILABLE = True
except ImportError:
    YOOKASSA_AVAILABLE = False
    print("[YooKassa] SDK not installed. Run: pip install yookassa")


class YooKassaService:
    """Сервис для работы с YooKassa"""

    def __init__(self):
        self.settings = get_settings()
        self._configure()

    def _configure(self):
        """Настроить YooKassa SDK"""
        if not YOOKASSA_AVAILABLE:
            return

        if self.settings.yookassa_shop_id and self.settings.yookassa_secret_key:
            Configuration.account_id = self.settings.yookassa_shop_id
            Configuration.secret_key = self.settings.yookassa_secret_key
            print(f"[YooKassa] Configured with shop_id: {self.settings.yookassa_shop_id}")
        else:
            print("[YooKassa] Not configured: missing shop_id or secret_key")

    def is_configured(self) -> bool:
        """Проверить, настроен ли сервис"""
        return (
            YOOKASSA_AVAILABLE
            and bool(self.settings.yookassa_shop_id)
            and bool(self.settings.yookassa_secret_key)
        )

    async def create_subscription_payment(
        self,
        user_id: str,
        email: str,
        description: str = "Подписка Pro на 1 месяц",
    ) -> Optional[dict]:
        """
        Создать платёж для подписки Pro.
        Возвращает:
        - payment_id: ID платежа в YooKassa
        - confirmation_url: URL для редиректа пользователя
        """
        if not self.is_configured():
            print("[YooKassa] Not configured, cannot create payment")
            return None

        try:
            idempotence_key = str(uuid.uuid4())

            payment = Payment.create({
                "amount": {
                    "value": str(self.settings.subscription_price),
                    "currency": "RUB",
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": self.settings.yookassa_return_url,
                },
                "capture": True,  # Автоматическое подтверждение
                "description": description,
                "metadata": {
                    "user_id": user_id,
                    "type": "subscription",
                    "plan": "pro",
                },
                "receipt": {
                    "customer": {
                        "email": email,
                    },
                    "items": [
                        {
                            "description": description,
                            "quantity": "1",
                            "amount": {
                                "value": str(self.settings.subscription_price),
                                "currency": "RUB",
                            },
                            "vat_code": 1,  # Без НДС
                            "payment_mode": "full_payment",
                            "payment_subject": "service",
                        }
                    ],
                },
            }, idempotence_key)

            return {
                "payment_id": payment.id,
                "confirmation_url": payment.confirmation.confirmation_url,
                "status": payment.status,
            }

        except Exception as e:
            print(f"[YooKassa] create_subscription_payment error: {e}")
            return None

    async def create_extra_requests_payment(
        self,
        user_id: str,
        email: str,
        description: str = None,
    ) -> Optional[dict]:
        """
        Создать платёж для докупки запросов.
        """
        if not self.is_configured():
            print("[YooKassa] Not configured, cannot create payment")
            return None

        if description is None:
            description = f"Дополнительные запросы ({self.settings.extra_requests_count} шт.)"

        try:
            idempotence_key = str(uuid.uuid4())

            payment = Payment.create({
                "amount": {
                    "value": str(self.settings.extra_requests_price),
                    "currency": "RUB",
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": self.settings.yookassa_return_url + "?type=extra",
                },
                "capture": True,
                "description": description,
                "metadata": {
                    "user_id": user_id,
                    "type": "extra_requests",
                    "count": self.settings.extra_requests_count,
                },
                "receipt": {
                    "customer": {
                        "email": email,
                    },
                    "items": [
                        {
                            "description": description,
                            "quantity": "1",
                            "amount": {
                                "value": str(self.settings.extra_requests_price),
                                "currency": "RUB",
                            },
                            "vat_code": 1,
                            "payment_mode": "full_payment",
                            "payment_subject": "service",
                        }
                    ],
                },
            }, idempotence_key)

            return {
                "payment_id": payment.id,
                "confirmation_url": payment.confirmation.confirmation_url,
                "status": payment.status,
            }

        except Exception as e:
            print(f"[YooKassa] create_extra_requests_payment error: {e}")
            return None

    async def check_payment_status(self, payment_id: str) -> Optional[dict]:
        """Проверить статус платежа"""
        if not self.is_configured():
            return None

        try:
            payment = Payment.find_one(payment_id)
            return {
                "id": payment.id,
                "status": payment.status,
                "paid": payment.paid,
                "amount": payment.amount.value,
                "metadata": payment.metadata,
                "created_at": payment.created_at,
            }
        except Exception as e:
            print(f"[YooKassa] check_payment_status error: {e}")
            return None

    def parse_webhook(self, body: bytes) -> Optional[dict]:
        """
        Парсить webhook уведомление от YooKassa.
        Возвращает данные события или None при ошибке.
        """
        if not YOOKASSA_AVAILABLE:
            return None

        try:
            notification = WebhookNotification(json.loads(body))
            event_type = notification.event

            payment = notification.object
            metadata = payment.metadata or {}

            return {
                "event": event_type,
                "payment_id": payment.id,
                "status": payment.status,
                "paid": payment.paid,
                "amount": payment.amount.value,
                "user_id": metadata.get("user_id"),
                "type": metadata.get("type"),  # subscription или extra_requests
                "plan": metadata.get("plan"),
                "count": metadata.get("count"),  # для extra_requests
            }

        except Exception as e:
            print(f"[YooKassa] parse_webhook error: {e}")
            return None

    def verify_webhook_signature(
        self,
        body: bytes,
        signature: str,
    ) -> bool:
        """
        Проверить подпись webhook (если настроено).
        YooKassa использует IP-whitelist вместо подписи,
        но можно добавить дополнительную проверку.
        """
        # YooKassa не использует подпись по умолчанию
        # Проверка идёт по IP-адресу
        return True


# Синглтон
yookassa_service = YooKassaService()
