"""
API эндпоинты для чата
Упрощенная архитектура: извлечение параметров → поиск → валидация → результат
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json
import asyncio
from datetime import datetime


def json_serializer(obj):
    """Сериализатор для datetime и других объектов"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

from models.chat import ChatRequest, ChatResponse, UserPreferences
from models.vacancy import SearchFilters
from agents.param_extractor import param_extractor
from agents.validator import validator
from tools.search import vacancy_search
from services.user_profile import user_profile_service
from services.subscription_service import subscription_service
from config import get_settings

router = APIRouter(prefix="/chat", tags=["chat"])

# Временное хранилище сессий (в проде использовать Redis)
sessions: dict[str, dict] = {}


class ChatMessageRequest(BaseModel):
    message: str
    chat_id: Optional[str] = None
    user_id: str
    search_in_feed: bool = True  # Поиск в ленте (БД)
    search_online: bool = True   # Поиск в сети (live)
    exclude_vacancy_ids: list[str] = []  # ID вакансий для исключения


class ChatMessageResponse(BaseModel):
    message: str
    vacancies: list[dict] = []
    chat_id: str


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: ChatMessageRequest):
    """
    Отправка сообщения в чат
    Простой поток: извлечение параметров → поиск → валидация → результат
    """
    # Проверяем лимит запросов
    can_use, limit_message = await subscription_service.check_and_use_request(request.user_id)
    if not can_use:
        raise HTTPException(status_code=402, detail=limit_message)

    # Проверяем доступ к поиску в сети (Base план не имеет доступа)
    status = await subscription_service.get_full_status(request.user_id)
    if status.subscription and not status.subscription.can_search_online:
        request.search_online = False  # Принудительно отключаем для Base плана

    # Получаем или создаём сессию
    session_key = f"{request.user_id}_{request.chat_id or 'new'}"

    if session_key not in sessions:
        sessions[session_key] = {
            "history": [],
            "preferences": UserPreferences(),
        }

    session = sessions[session_key]

    # Получаем данные профиля пользователя
    user_data = await user_profile_service.get_user_data(request.user_id)

    # === ШАГ 1: Извлекаем параметры из сообщения ===
    preferences, needs_clarification, clarification_msg = await param_extractor.extract(
        request.message,
        session["preferences"],
        user_data,
        session["history"],  # Передаем историю для контекста
    )
    session["preferences"] = preferences

    print(f"[Chat] Extracted: query={preferences.query}, city={preferences.city}, salary={preferences.salary_from}")

    # Добавляем сообщение в историю
    session["history"].append({
        "role": "user",
        "content": request.message,
    })

    # === ШАГ 2: Если нужна дополнительная информация - спрашиваем ===
    if needs_clarification:
        response_message = clarification_msg or "Уточни детали пожалуйста"

        session["history"].append({
            "role": "assistant",
            "content": response_message,
        })

        return ChatMessageResponse(
            message=response_message,
            vacancies=[],
            chat_id=session_key,
        )

    # === ШАГ 3: Генерируем варианты запросов ===
    queries = await param_extractor.generate_queries(preferences.query)
    print(f"[Chat] Generated queries: {queries}")

    # === ШАГ 4: Поэтапный поиск и валидация ===
    all_validated = []
    all_rejected = []
    total_found = 0

    # Режим: Лента + Сеть (поэтапная валидация)
    if request.search_in_feed and request.search_online:
        # 4.1: Поиск в БД
        db_filters = SearchFilters(
            queries=queries,
            city=preferences.city,
            salary_from=preferences.salary_from,
            salary_to=preferences.salary_to,
            experience=preferences.experience,
            employment_type=preferences.employment_type,
            exclude_keywords=preferences.exclude_keywords,
            exclude_vacancy_ids=request.exclude_vacancy_ids,
            search_in_feed=True,
            search_online=False,
        )
        db_result = await vacancy_search.search(db_filters)
        print(f"[Chat] БД: найдено {len(db_result.vacancies)} вакансий")
        total_found += db_result.total_found

        # 4.2: Валидация БД
        db_validation = await validator.validate_batch(
            db_result.vacancies,
            preferences,
            queries=queries,
            required_city=preferences.city,
        )
        all_validated.extend(db_validation.validated)
        all_rejected.extend(db_validation.rejected)
        print(f"[Chat] БД: {len(db_validation.validated)} подходящих")

        # 4.3: Поиск в сети (ВСЕГДА, независимо от результатов БД)
        online_filters = SearchFilters(
            queries=queries,
            city=preferences.city,
            salary_from=preferences.salary_from,
            salary_to=preferences.salary_to,
            experience=preferences.experience,
            employment_type=preferences.employment_type,
            exclude_keywords=preferences.exclude_keywords,
            exclude_vacancy_ids=request.exclude_vacancy_ids,
            search_in_feed=False,
            search_online=True,
        )
        online_result = await vacancy_search.search(online_filters)
        print(f"[Chat] Сеть: найдено {len(online_result.vacancies)} вакансий")
        total_found += online_result.total_found

        # 4.4: Валидация сети (исключая дубликаты из БД)
        seen_ids = {v.id for v in all_validated}
        unique_online = [v for v in online_result.vacancies if v.id not in seen_ids]
        print(f"[Chat] Сеть: {len(unique_online)} уникальных после дедупликации")

        online_validation = await validator.validate_batch(
            unique_online,
            preferences,
            queries=queries,
            required_city=preferences.city,
        )
        all_validated.extend(online_validation.validated)
        all_rejected.extend(online_validation.rejected)
        print(f"[Chat] Сеть: {len(online_validation.validated)} подходящих")

    # Режим: Только лента ИЛИ только сеть
    else:
        filters = SearchFilters(
            queries=queries,
            city=preferences.city,
            salary_from=preferences.salary_from,
            salary_to=preferences.salary_to,
            experience=preferences.experience,
            employment_type=preferences.employment_type,
            exclude_keywords=preferences.exclude_keywords,
            exclude_vacancy_ids=request.exclude_vacancy_ids,
            search_in_feed=request.search_in_feed,
            search_online=request.search_online,
        )

        search_result = await vacancy_search.search(filters)
        total_found = search_result.total_found
        print(f"[Chat] Найдено {len(search_result.vacancies)} вакансий")

        validation_result = await validator.validate_batch(
            search_result.vacancies,
            preferences,
            queries=queries,
            required_city=preferences.city,
        )
        all_validated = validation_result.validated
        all_rejected = validation_result.rejected
        print(f"[Chat] {len(all_validated)} подходящих")

    # === ШАГ 5: Формируем ответ ===
    response_message = f"Нашёл {len(all_validated)} подходящих вакансий из {total_found} найденных."

    vacancies_json = [v.model_dump(mode='json') for v in all_validated]
    rejected_json = [v.model_dump(mode='json') for v in all_rejected]

    session["history"].append({
        "role": "assistant",
        "content": response_message,
    })

    return ChatMessageResponse(
        message=response_message,
        vacancies=vacancies_json,
        chat_id=session_key,
    )


@router.post("/message/stream")
async def send_message_stream(request: ChatMessageRequest):
    """
    Отправка сообщения со стримингом ответа
    Простой поток: извлечение параметров → поиск → валидация → результат
    """
    # Проверяем лимит запросов
    can_use, limit_message = await subscription_service.check_and_use_request(request.user_id)
    if not can_use:
        raise HTTPException(status_code=402, detail=limit_message)

    # Проверяем доступ к поиску в сети (Base план не имеет доступа)
    status = await subscription_service.get_full_status(request.user_id)
    if status.subscription and not status.subscription.can_search_online:
        request.search_online = False  # Принудительно отключаем для Base плана

    session_key = f"{request.user_id}_{request.chat_id or 'new'}"

    if session_key not in sessions:
        sessions[session_key] = {
            "history": [],
            "preferences": UserPreferences(),
        }

    session = sessions[session_key]

    # Получаем данные профиля пользователя
    user_data = await user_profile_service.get_user_data(request.user_id)

    session["history"].append({
        "role": "user",
        "content": request.message,
    })

    async def generate():
        """Генератор для SSE"""

        # === ШАГ 1: Извлекаем параметры ===
        preferences, needs_clarification, clarification_msg = await param_extractor.extract(
            request.message,
            session["preferences"],
            user_data,
            session["history"],  # Передаем историю для контекста
        )
        session["preferences"] = preferences

        # === ШАГ 2: Если нужна информация - спрашиваем ===
        if needs_clarification:
            response_message = clarification_msg or "Уточни детали пожалуйста"

            # Стримим ответ
            words = response_message.split()
            for word in words:
                yield f"data: {json.dumps({'type': 'text', 'content': word + ' '})}\n\n"
                await asyncio.sleep(0.03)

            yield f"data: {json.dumps({'type': 'done', 'chat_id': session_key})}\n\n"

            session["history"].append({
                "role": "assistant",
                "content": response_message,
            })
            return

        # === ШАГ 3: Генерируем варианты запросов ===
        queries = await param_extractor.generate_queries(preferences.query)

        # === ШАГ 4: Поэтапный поиск и валидация ===
        all_validated = []
        all_rejected = []
        total_found = 0
        seen_ids = set()

        # Отправляем начальное сообщение
        yield f"data: {json.dumps({'type': 'text', 'content': 'Ищу вакансии '})}\n\n"
        await asyncio.sleep(0.05)

        # Режим: Лента + Сеть (поэтапная валидация с progressive loading)
        if request.search_in_feed and request.search_online:
            # 4.1: Поиск в БД
            db_filters = SearchFilters(
                queries=queries,
                city=preferences.city,
                salary_from=preferences.salary_from,
                salary_to=preferences.salary_to,
                experience=preferences.experience,
                employment_type=preferences.employment_type,
                exclude_keywords=preferences.exclude_keywords,
                exclude_vacancy_ids=request.exclude_vacancy_ids,
                search_in_feed=True,
                search_online=False,
            )
            db_result = await vacancy_search.search(db_filters)
            total_found += db_result.total_found

            # 4.2: Валидация БД
            # Добавляем ВСЕ ID из БД в seen_ids (чтобы не было дубликатов)
            seen_ids.update(v.id for v in db_result.vacancies)

            db_validation = await validator.validate_batch(
                db_result.vacancies,
                preferences,
                queries=queries,
                required_city=preferences.city,
            )
            all_validated.extend(db_validation.validated)
            all_rejected.extend(db_validation.rejected)

            # Отправляем результаты БД сразу
            if db_validation.validated:
                db_vacancies_json = [v.model_dump(mode='json') for v in db_validation.validated]
                yield f"data: {json.dumps({'type': 'vacancies_chunk', 'content': db_vacancies_json, 'source': 'database'}, default=json_serializer)}\n\n"
                yield f"data: {json.dumps({'type': 'text', 'content': f'(найдено {len(all_validated)} из базы) '})}\n\n"

            # 4.3: Быстрый поиск (HH + SuperJob) БЕЗ Avito
            fast_result = await vacancy_search._search_live_fast(
                SearchFilters(
                    queries=queries,
                    city=preferences.city,
                    salary_from=preferences.salary_from,
                    salary_to=preferences.salary_to,
                    experience=preferences.experience,
                    employment_type=preferences.employment_type,
                    exclude_keywords=preferences.exclude_keywords,
                    exclude_vacancy_ids=request.exclude_vacancy_ids,
                )
            )
            total_found += len(fast_result)

            # Валидация быстрых результатов (исключаем дубликаты)
            unique_fast = [v for v in fast_result if v.id not in seen_ids]
            # Добавляем ID в seen_ids ДО валидации
            seen_ids.update(v.id for v in unique_fast)

            if unique_fast:
                fast_validation = await validator.validate_batch(
                    unique_fast,
                    preferences,
                    queries=queries,
                    required_city=preferences.city,
                )
                all_validated.extend(fast_validation.validated)
                all_rejected.extend(fast_validation.rejected)

                # Отправляем HH + SuperJob сразу
                if fast_validation.validated:
                    fast_vacancies_json = [v.model_dump(mode='json') for v in fast_validation.validated]
                    yield f"data: {json.dumps({'type': 'vacancies_chunk', 'content': fast_vacancies_json, 'source': 'hh_superjob'}, default=json_serializer)}\n\n"
                    yield f"data: {json.dumps({'type': 'text', 'content': f'(+{len(fast_validation.validated)} из HH/SuperJob) '})}\n\n"

            # 4.4: Медленный поиск Avito (в фоне, стримим по мере готовности)
            from tools.parsers.avito import AvitoParser
            avito_parser = AvitoParser()
            try:
                avito_filters = SearchFilters(
                    query=queries[0] if queries else "",
                    city=preferences.city,
                    salary_from=preferences.salary_from,
                    salary_to=preferences.salary_to,
                    experience=preferences.experience,
                    employment_type=preferences.employment_type,
                    exclude_keywords=preferences.exclude_keywords,
                    exclude_vacancy_ids=request.exclude_vacancy_ids,
                )
                avito_result = await avito_parser.search(avito_filters, limit=200)
                total_found += len(avito_result)

                # Валидация Avito (исключаем дубликаты)
                unique_avito = [v for v in avito_result if v.id not in seen_ids]
                # Добавляем ID в seen_ids ДО валидации
                seen_ids.update(v.id for v in unique_avito)

                if unique_avito:
                    avito_validation = await validator.validate_batch(
                        unique_avito,
                        preferences,
                        queries=queries,
                        required_city=preferences.city,
                    )
                    all_validated.extend(avito_validation.validated)
                    all_rejected.extend(avito_validation.rejected)

                    # Отправляем Avito результаты
                    if avito_validation.validated:
                        avito_vacancies_json = [v.model_dump(mode='json') for v in avito_validation.validated]
                        yield f"data: {json.dumps({'type': 'vacancies_chunk', 'content': avito_vacancies_json, 'source': 'avito'}, default=json_serializer)}\n\n"
                        yield f"data: {json.dumps({'type': 'text', 'content': f'(+{len(avito_validation.validated)} из Avito) '})}\n\n"

            except Exception as e:
                print(f"[Chat] Avito error: {e}")
                yield f"data: {json.dumps({'type': 'progress', 'message': 'Ошибка загрузки с Avito, продолжаю...'})}\n\n"

        # Режим: Только лента ИЛИ только сеть
        else:
            filters = SearchFilters(
                queries=queries,
                city=preferences.city,
                salary_from=preferences.salary_from,
                salary_to=preferences.salary_to,
                experience=preferences.experience,
                employment_type=preferences.employment_type,
                exclude_keywords=preferences.exclude_keywords,
                exclude_vacancy_ids=request.exclude_vacancy_ids,
                search_in_feed=request.search_in_feed,
                search_online=request.search_online,
            )

            search_result = await vacancy_search.search(filters)
            total_found = search_result.total_found

            validation_result = await validator.validate_batch(
                search_result.vacancies,
                preferences,
                queries=queries,
                required_city=preferences.city,
            )
            all_validated = validation_result.validated
            all_rejected = validation_result.rejected

        # === ШАГ 5: Итоговое сообщение ===
        response_message = f"\n\nИтого: {len(all_validated)} подходящих вакансий из {total_found} найденных."

        # Для progressive loading режима - просто добавляем итоговое сообщение
        if request.search_in_feed and request.search_online:
            yield f"data: {json.dumps({'type': 'text', 'content': response_message})}\n\n"

            # Отправляем отсеянные
            rejected_json = [v.model_dump(mode='json') for v in all_rejected]
            if rejected_json:
                yield f"data: {json.dumps({'type': 'rejected_vacancies', 'content': rejected_json}, default=json_serializer)}\n\n"

        # Для обычного режима - стримим ответ и отправляем все вакансии
        else:
            words = response_message.split()
            for word in words:
                yield f"data: {json.dumps({'type': 'text', 'content': word + ' '})}\n\n"
                await asyncio.sleep(0.03)

            # Отправляем вакансии
            vacancies_json = [v.model_dump(mode='json') for v in all_validated]
            if vacancies_json:
                yield f"data: {json.dumps({'type': 'vacancies', 'content': vacancies_json}, default=json_serializer)}\n\n"

            # Отправляем отсеянные
            rejected_json = [v.model_dump(mode='json') for v in all_rejected]
            if rejected_json:
                yield f"data: {json.dumps({'type': 'rejected_vacancies', 'content': rejected_json}, default=json_serializer)}\n\n"

        # Завершение
        yield f"data: {json.dumps({'type': 'done', 'chat_id': session_key})}\n\n"

        session["history"].append({
            "role": "assistant",
            "content": response_message,
        })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str, user_id: str):
    """Удаление чата"""
    session_key = f"{user_id}_{chat_id}"
    if session_key in sessions:
        del sessions[session_key]
    return {"status": "ok"}


@router.get("/preferences/{user_id}")
async def get_preferences(user_id: str, chat_id: Optional[str] = None):
    """Получение текущих предпочтений"""
    session_key = f"{user_id}_{chat_id or 'new'}"
    if session_key in sessions:
        return sessions[session_key]["preferences"].model_dump()
    return UserPreferences().model_dump()


# === Support Chat (FloatingChat) ===

class SupportChatRequest(BaseModel):
    message: str
    context: Optional[str] = None


class SupportChatResponse(BaseModel):
    response: str
    connect_to_admin: bool = False


# Ключевые слова для определения запроса к администратору
ADMIN_KEYWORDS = [
    "человек", "живой", "оператор", "администратор", "админ", "поддержка",
    "связаться", "позвонить", "менеджер", "консультант", "помощь живого",
    "реальный человек", "не бот", "хочу поговорить", "нужна помощь человека",
    "соединить с", "переключить на", "написать админу", "жалоба",
    "проблема с оплатой", "не работает оплата", "возврат денег", "отменить подписку",
]

import re

ADMIN_PATTERNS = [
    r"свяжи(те)?.*с.*человек",
    r"хочу.*говорить.*с.*человек",
    r"нужен.*живой",
    r"перевед(и|ите).*на.*оператор",
    r"можно.*поговорить.*с",
    r"есть.*живой.*оператор",
    r"как.*связаться.*с.*поддержк",
    r"хочу.*пожаловаться",
    r"вернуть.*деньги",
]


def detect_admin_request(message: str) -> bool:
    """Определяет, хочет ли пользователь связаться с админом"""
    lower_message = message.lower()

    for keyword in ADMIN_KEYWORDS:
        if keyword in lower_message:
            return True

    for pattern in ADMIN_PATTERNS:
        if re.search(pattern, lower_message, re.IGNORECASE):
            return True

    return False


@router.post("/support", response_model=SupportChatResponse)
async def support_chat(request: SupportChatRequest):
    """
    AI-чат поддержки для FloatingChat (не поиск вакансий)
    """
    import httpx

    settings = get_settings()
    needs_admin = detect_admin_request(request.message)

    system_prompt = (
        "Ты дружелюбный помощник на сайте поиска работы JobAISearch.\n"
        "Пользователь хочет связаться с живым администратором.\n"
        "Скажи что понимаешь его и сейчас подключишь к администратору.\n"
        "Будь вежлив и краток. Ответь на русском языке."
        if needs_admin else
        "Ты дружелюбный помощник на сайте поиска работы JobAISearch.\n"
        "Отвечай кратко и по делу на русском языке.\n"
        "Помогай пользователям с вопросами о поиске работы, резюме и функциях сайта.\n\n"
        "Основные функции сайта:\n"
        "- AI-поиск вакансий через чат\n"
        "- Лента вакансий с фильтрами\n"
        "- Создание резюме в профиле\n"
        "- Чат с работодателями\n"
        "- Pro Trial: 7 дней бесплатно при регистрации, 15 запросов/день, полный доступ\n"
        "- Base: бесплатный план навсегда, 3 запроса/день, поиск только в ленте\n"
        "- Pro: 799₽/мес, 15 запросов/день, полный поиск включая сеть\n\n"
        "Если не можешь помочь с вопросом, предложи связаться с администратором."
    )

    try:
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
                        {"role": "user", "content": request.message},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                ai_response = data["choices"][0]["message"]["content"]
                return SupportChatResponse(response=ai_response, connect_to_admin=needs_admin)
            else:
                print(f"[Support Chat] Error: {response.status_code} {response.text}")
                return SupportChatResponse(
                    response="Подключаю вас к администратору..." if needs_admin else "Извините, произошла ошибка. Попробуйте позже.",
                    connect_to_admin=needs_admin
                )
    except Exception as e:
        print(f"[Support Chat] Exception: {e}")
        return SupportChatResponse(
            response="Подключаю вас к администратору..." if needs_admin else "Извините, произошла ошибка. Попробуйте позже.",
            connect_to_admin=needs_admin
        )
