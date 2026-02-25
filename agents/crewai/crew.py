"""
JobSearchCrew - мультиагентная система поиска работы.
Реализация без зависимости от crewai/langchain - прямые вызовы OpenRouter API.
"""
import json
import re
from typing import Optional, AsyncIterator, Any
from dataclasses import dataclass, field

from .llm_config import get_main_llm, get_fast_llm
from .session_manager import Session, SessionManager, session_manager
from agents.agents_config import is_agent_enabled

# Импорты для поиска
from tools.search import vacancy_search
from models.vacancy import SearchFilters
from services.user_profile import user_profile_service
from services.lifestyle_matcher import lifestyle_matcher


@dataclass
class CrewResult:
    """Результат работы Crew."""
    response_text: str
    vacancies: list = field(default_factory=list)
    rejected_vacancies: list = field(default_factory=list)
    request_type: str = "CHAT"
    show_vacancies: bool = True
    suggested_actions: list = field(default_factory=list)


# === СИСТЕМНЫЕ ПРОМПТЫ АГЕНТОВ ===

ANALYST_SYSTEM_PROMPT = """Ты - аналитик карьерного AI-партнёра. Поиск вакансий - это инструмент, а твоя задача - понять намерение пользователя, сохранить контекст диалога и извлечь параметры поиска.

ПРАВИЛА:
1. Анализируй сообщение пользователя и извлеки параметры
2. Если запрос размытый (нет профессии или города) - задай ОДИН уточняющий вопрос
3. Учитывай контекст предыдущих сообщений
4. Определи тип запроса: SEARCH, CLARIFICATION, CHAT, PROFILE
5. Если пользователь пишет короткую команду продолжения ("найди еще", "покажи еще", "ещё варианты", "такие же"), используй контекст предыдущего поиска и НЕ начинай анализ как новый пустой запрос

ПАРАМЕТРЫ ДЛЯ ИЗВЛЕЧЕНИЯ:
- professions: список профессий (может быть несколько!)
- company: компания/бренд (если пользователь ищет работу в конкретной компании)
- strict_company: true если нужно искать ТОЛЬКО в этой компании
- city: город
- salary_from: минимальная зарплата (число)
- salary_to: максимальная зарплата (число)
- experience: опыт (no_experience, 1-3, 3-6, 6+)
- employment_type: тип занятости (full, part, remote)
- exclude_keywords: что исключить

КОГДА ЗАДАВАТЬ ВОПРОС (CLARIFICATION):
- Нет профессии И нет контекста → спроси про профессию
- Есть профессия, но очень общая ("работа", "нормальная работа") → уточни сферу
- НЕ спрашивай про зарплату если уже указана
- НЕ спрашивай про город если уже указан
- Если пользователь указал конкретную компанию + город ("в СДЭК в Новороссийске") → это SEARCH, даже без профессии
- Если пользователь пишет "найди еще"/"покажи еще"/"ещё такие же" и в истории уже был поиск → это SEARCH по тем же параметрам, без повторных уточнений
- Задавай ОДИН вопрос, не несколько сразу

ПРИМЕРЫ:
"Найди работу в Москве от 80к" → CLARIFICATION (нет профессии)
"Продавец в Краснодаре" → SEARCH (всё ясно)
"Ищу работу продавцом или администратором в Новороссийске от 40к" → SEARCH (несколько профессий)
"Нужна работа на удаленке по IT от 60к" → SEARCH (employment_type: "remote")
"Ищу разработчика на удалёнке" → SEARCH (employment_type: "remote")
"Удаленная работа бухгалтером" → SEARCH (employment_type: "remote")
"Работа удаленно" → SEARCH (employment_type: "remote", но нет профессии - спроси)
"Ищу работу в СДЭК в Новороссийске" → SEARCH (company="СДЭК", strict_company=true)
"найди еще" (после выданных вакансий) → SEARCH (взять параметры из контекста)
"Привет, как дела?" → CHAT
"Что у меня в резюме?" → PROFILE

ВАЖНО - РАСПОЗНАВАНИЕ ТИПА ЗАНЯТОСТИ:
- "на удаленке", "на удалёнке", "удаленная работа", "удалённая работа", "удаленно", "удалённо", "remote", "дистанционно" → employment_type: "remote"
- "полный день", "полная занятость" → employment_type: "full"
- "частичная занятость", "неполный день", "подработка" → employment_type: "part"

ФОРМАТ ОТВЕТА (строго JSON):
{
    "request_type": "SEARCH" | "CLARIFICATION" | "CHAT" | "PROFILE",
    "parameters": {
        "professions": ["профессия1", "профессия2"],
        "company": "название компании" | null,
        "strict_company": true | false,
        "city": "город" | null,
        "salary_from": число | null,
        "salary_to": число | null,
        "experience": "строка" | null,
        "employment_type": "строка" | null,
        "exclude_keywords": []
    },
    "clarification_question": "вопрос если тип CLARIFICATION" | null,
    "search_ready": true | false
}"""

STRATEGIST_SYSTEM_PROMPT = """Ты - стратег поиска вакансий. Генерируешь оптимальные поисковые запросы на основе параметров.

ПРАВИЛА:
1. Для каждой профессии генерируй 3-5 вариантов запроса (синонимы, вариации)
2. Учитывай разговорные названия профессий
3. Не дублируй запросы
4. ВАЖНО: НЕ заменяй профессию на смежные профессии!
5. Если задана company и strict_company=true, ВСЕ запросы должны содержать эту компанию
6. Если strict_company=true, запрещено добавлять другие компании в queries

ЗАПРЕЩЕНИЯ (категорически запрещённые замены):
- Продавец → НЕ добавляй: мерчандайзер, кладовщик, склад, комплектовщик, фасовщик
- Водитель → НЕ добавляй: курьер, экспедитор, логист (если не просили)
- Менеджер → НЕ добавляй: мерчандайзер, администратор
- Курьер → НЕ добавляй: водитель, экспедитор

СЛОВАРЬ СИНОНИМОВ:
- Продавец → продавец, продавец-консультант, продавец-кассир, менеджер торгового зала
- Администратор → администратор, офис-менеджер, управляющий, администратор зала
- Водитель → водитель, водитель категории B, водитель-курьер, водитель-экспедитор
- Программист → программист, разработчик, developer, software engineer
- Бухгалтер → бухгалтер, главный бухгалтер, бухгалтер на участок
- Менеджер → менеджер по продажам, sales manager, менеджер по работе с клиентами
- Курьер → курьер, доставщик, курьер пеший, курьер на авто
- ПВЗ → сотрудник ПВЗ, оператор пункта выдачи, менеджер ПВЗ, кладовщик ПВЗ

ФОРМАТ ОТВЕТА (JSON):
{
    "queries": ["запрос1", "запрос2", ...],
    "search_config": {
        "city": "город",
        "salary_from": число | null,
        "salary_to": число | null,
        "company": "название компании" | null,
        "strict_company": true | false
    }
}"""

VALIDATOR_SYSTEM_PROMPT = """Ты - валидатор вакансий. Проверяешь релевантность вакансий запросу пользователя.

ПРАВИЛА ПРОВЕРКИ:
1. Город должен совпадать (если указан)
2. Профессия должна быть релевантна
3. Зарплата должна быть >= указанной (если указана)
4. Отсеивай подозрительные (военные вербовки, MLM, мошенничество)

РЕЙТИНГ:
- A: идеально подходит (город + профессия + зарплата совпадают)
- B: хорошо подходит (2 из 3 параметров совпадают)
- C: может подойти (1 параметр совпадает или близко)
- REJECT: не подходит

ФОРМАТ ОТВЕТА (JSON):
{
    "validated": [
        {"id": "id_вакансии", "rating": "A"|"B"|"C", "reason": "причина"}
    ],
    "rejected": [
        {"id": "id_вакансии", "reason": "причина отклонения"}
    ],
    "stats": {
        "total": число,
        "accepted": число,
        "rating_a": число,
        "rating_b": число,
        "rating_c": число
    }
}"""

COMPOSER_SYSTEM_PROMPT = """Ты - карьерный консультант и личный AI-партнёр пользователя. Поиск вакансий - только один из инструментов. Формируешь дружелюбные, краткие и полезные ответы.

ПРАВИЛА:
1. КРАТКОСТЬ: 1-3 предложения максимум
2. КОНКРЕТИКА: "Нашёл 23 вакансии" а не "нашёл несколько"
3. ТОН: дружелюбный, но профессиональный
4. БЕЗ канцеляризмов и официоза
5. НЕ перечисляй вакансии в тексте - они показываются отдельно карточками
6. Показывай, что ты помнишь контекст текущего разговора и продолжаешь линию пользователя

ПРИ УТОЧНЕНИИ:
- Подтверди что понял
- Задай ОДИН вопрос
- Пример: "Понял, ищем в Москве от 80 тысяч. А какая профессия интересует?"

ПРИ ПОИСКЕ:
- Краткое резюме: "Нашёл X вакансий [профессия] в [город]."
- Если это продолжение поиска ("ещё") - явно скажи что продолжаешь подбор по тем же критериям
- Если мало (<5): предложи расширить, НЕ извиняйся

ПРИ ЧАТЕ:
- Ответь дружелюбно
- Помогай как карьерный консультант: про цели, направление, стратегию поиска, резюме, отклики, собеседования
- Направь к поиску только если это действительно уместно

ЗАПРЕЩЕНО:
- "Здравствуйте! Я рад помочь..."
- Длинные вступления
- Извинения за малое количество
- Технические детали

Отвечай ТОЛЬКО текст ответа, без JSON."""


class JobSearchCrew:
    """
    Мультиагентная система для поиска работы.
    Использует прямые вызовы OpenRouter API.
    """

    COMPANY_ALIAS_MAP = {
        "сдэк": {"сдэк", "cdek", "sdek"},
        "cdek": {"сдэк", "cdek", "sdek"},
        "sdek": {"сдэк", "cdek", "sdek"},
        "озон": {"озон", "ozon"},
        "ozon": {"озон", "ozon"},
        "вайлдберриз": {"вайлдберриз", "wildberries", "wb", "вб"},
        "wildberries": {"вайлдберриз", "wildberries", "wb", "вб"},
        "яндекс": {"яндекс", "yandex"},
        "yandex": {"яндекс", "yandex"},
        "боксберри": {"боксберри", "boxberry"},
        "boxberry": {"боксберри", "boxberry"},
    }

    SIMILAR_COMPANIES_MAP = {
        "сдэк": ["Ozon", "Wildberries", "Яндекс Доставка", "Boxberry"],
        "cdek": ["Ozon", "Wildberries", "Яндекс Доставка", "Boxberry"],
        "sdek": ["Ozon", "Wildberries", "Яндекс Доставка", "Boxberry"],
        "ozon": ["СДЭК", "Wildberries", "Яндекс Доставка"],
        "озон": ["СДЭК", "Wildberries", "Яндекс Доставка"],
        "wildberries": ["СДЭК", "Ozon", "Яндекс Доставка"],
        "вайлдберриз": ["СДЭК", "Ozon", "Яндекс Доставка"],
    }

    def __init__(
        self,
        user_id: str,
        user_data: Optional[dict] = None,
        session: Optional[Session] = None,
    ):
        self.user_id = user_id
        self.user_data = user_data or {}
        self.session = session
        self.main_llm = get_main_llm()
        self.fast_llm = get_fast_llm()

    async def process_message(
        self,
        message: str,
        conversation_history: list[dict] = None,
        use_live_search: bool = True,
        use_feed_search: bool = True,
        exclude_vacancy_ids: Optional[list[str]] = None,
        lifestyle_preferences: Optional[dict] = None,
    ) -> CrewResult:
        """Обработать сообщение пользователя."""
        history = conversation_history or []
        context_summary = ""
        if self.session:
            context_summary = self.session.get_context_summary()

        user_name = self.user_data.get("name") or self.user_data.get("first_name")

        pending_response = self._handle_pending_similar_offer_message(message)
        if pending_response:
            return pending_response

        # Шаг 1: Анализ запроса
        print(f"[AI] Step 1: Analyzing request...")
        analysis = await self._analyze_request(message, history, context_summary)

        request_type = analysis.get("request_type", "CHAT")
        params = analysis.get("parameters", {}) or {}
        print(f"[AI] Request type: {request_type}")

        # Шаг 2: Обработка по типу
        if request_type == "CLARIFICATION":
            question = analysis.get("clarification_question", "")
            response_text = await self._compose_response(
                request_type="CLARIFICATION",
                context={
                    "parameters": params,
                    "question": question,
                },
                user_name=user_name,
            )
            return CrewResult(
                response_text=response_text,
                vacancies=[],
                request_type=request_type,
                show_vacancies=False,
            )

        elif request_type == "SEARCH":
            # Шаг 2: Генерация поисковых запросов
            print(f"[AI] Step 2: Creating search strategy...")
            strategy = await self._create_strategy(params)

            # Шаг 3: Выполнение поиска
            print(f"[AI] Step 3: Executing search...")
            vacancies = await self._execute_search(
                strategy,
                use_live_search=use_live_search,
                use_feed_search=use_feed_search,
                exclude_vacancy_ids=exclude_vacancy_ids,
            )
            print(f"[AI] Found {len(vacancies)} vacancies")

            # Шаг 4: Валидация
            validated_vacancies = vacancies
            rejected_vacancies: list = []
            if vacancies and len(vacancies) > 0:
                print(f"[AI] Step 4: Validating results...")
                validated_vacancies = await self._validate_results(vacancies, params)
                print(f"[AI] Validated: {len(validated_vacancies)} vacancies")
                validated_vacancies, rejected_vacancies = self._apply_lifestyle_preferences(
                    validated_vacancies,
                    lifestyle_preferences,
                )
                print(
                    f"[AI] Lifestyle filter: {len(validated_vacancies)} accepted, "
                    f"{len(rejected_vacancies)} rejected"
                )

            strict_company = bool(params.get("strict_company") and params.get("company"))
            if strict_company:
                validated_vacancies = self._filter_vacancies_by_company(
                    validated_vacancies,
                    str(params.get("company", "")),
                )

            if strict_company and not validated_vacancies:
                self._set_pending_similar_offer(params)
                return CrewResult(
                    response_text=self._build_no_company_results_message(params),
                    vacancies=[],
                    request_type="CLARIFICATION",
                    show_vacancies=False,
                    suggested_actions=["offer_similar_companies"],
                )

            self._clear_pending_similar_offer()

            # Шаг 5: Формирование ответа
            print(f"[AI] Step 5: Composing response...")
            response_text = await self._compose_response(
                request_type="SEARCH",
                context={
                    "parameters": params,
                    "total_found": len(vacancies),
                    "validated_count": len(validated_vacancies),
                },
                user_name=user_name,
            )

            # Обновляем сессию
            if self.session:
                if params.get("city"):
                    self.session.update_preferences(city=params["city"])
                if params.get("professions"):
                    self.session.update_preferences(query=", ".join(params["professions"]))
                if params.get("salary_from"):
                    self.session.update_preferences(salary_from=params["salary_from"])
                if params.get("employment_type"):
                    self.session.update_preferences(employment_type=params["employment_type"])

            return CrewResult(
                response_text=response_text,
                vacancies=validated_vacancies,
                rejected_vacancies=rejected_vacancies,
                request_type=request_type,
                show_vacancies=True,
            )

        else:
            # CHAT или PROFILE
            response_text = await self._compose_response(
                request_type=request_type,
                context={"message": message},
                user_name=user_name,
            )
            return CrewResult(
                response_text=response_text,
                vacancies=[],
                request_type=request_type,
                show_vacancies=False,
            )

    async def _analyze_request(
        self,
        message: str,
        history: list[dict],
        context_summary: str,
    ) -> dict:
        """Анализ запроса через LLM."""
        # Формируем контекст
        context_parts = []
        if context_summary:
            context_parts.append(f"Контекст сессии: {context_summary}")
        if self.user_data:
            if self.user_data.get("city"):
                context_parts.append(f"Город пользователя: {self.user_data['city']}")
            if self.user_data.get("desired_position"):
                context_parts.append(f"Желаемая должность: {self.user_data['desired_position']}")

        # Формируем историю для контекста
        history_text = ""
        if history:
            recent = history[-6:]  # Последние 6 сообщений
            history_lines = []
            for msg in recent:
                role = "Пользователь" if msg["role"] == "user" else "AI"
                history_lines.append(f"{role}: {msg['content'][:200]}")
            history_text = "\n".join(history_lines)

        user_prompt = f"""Сообщение пользователя: "{message}"

{"Предыдущий диалог:" + chr(10) + history_text if history_text else "Новый диалог."}

{chr(10).join(context_parts) if context_parts else ""}

Проанализируй запрос и верни JSON."""

        try:
            if not is_agent_enabled("crew_analyst"):
                print("[AI] Analyst DISABLED - defaulting to SEARCH")
                fallback = {"request_type": "SEARCH", "parameters": {"professions": [message], "city": ""}}
                return self._normalize_analysis_output(fallback, message)

            result = await self.main_llm.chat(
                system_prompt=ANALYST_SYSTEM_PROMPT,
                user_message=user_prompt,
                json_mode=True,
                agent_id="crew_analyst",
            )
            parsed = self._parse_json(result)
            return self._normalize_analysis_output(parsed, message)
        except Exception as e:
            print(f"[AI] Analyst error: {e}")
            return self._normalize_analysis_output({"request_type": "CHAT", "parameters": {}}, message)

    async def _create_strategy(self, params: dict) -> dict:
        """Генерация поисковых запросов."""
        professions = params.get("professions", [])
        company = params.get("company")
        strict_company = bool(params.get("strict_company") and company)
        city = params.get("city", "")
        salary = params.get("salary_from")
        employment_type = params.get("employment_type")  # remote, full, part

        if strict_company:
            strict_queries = self._build_strict_company_queries(professions, str(company), city)
            return {
                "queries": strict_queries,
                "search_config": {
                    "city": city,
                    "salary_from": salary,
                    "employment_type": employment_type,
                    "company": company,
                    "strict_company": True,
                },
            }

        user_prompt = f"""Параметры поиска:
- Профессии: {', '.join(professions) if professions else 'не указана'}
- Компания: {company or 'не указана'}
- Строго по компании: {'да' if strict_company else 'нет'}
- Город: {city or 'не указан'}
- Зарплата от: {salary or 'не указана'}
- Тип занятости: {employment_type or 'не указан'}

Сгенерируй поисковые запросы (JSON)."""

        try:
            if not is_agent_enabled("crew_strategist"):
                print("[AI] Strategist DISABLED - using professions directly")
                return {
                    "queries": professions if professions else ["работа"],
                    "search_config": {
                        "city": city,
                        "salary_from": salary,
                        "employment_type": employment_type,
                        "company": company,
                        "strict_company": strict_company,
                    },
                }

            result = await self.fast_llm.chat(
                system_prompt=STRATEGIST_SYSTEM_PROMPT,
                user_message=user_prompt,
                json_mode=True,
                agent_id="crew_strategist",
            )
            strategy = self._parse_json(result)
            queries = self._dedupe_queries(strategy.get("queries", []))
            if not queries:
                queries = professions if professions else ["работа"]

            search_config = strategy.get("search_config", {}) or {}
            search_config["city"] = city
            search_config["salary_from"] = salary
            search_config["employment_type"] = employment_type
            search_config["company"] = company
            search_config["strict_company"] = strict_company

            return {
                "queries": queries[:10],
                "search_config": search_config,
            }
        except Exception as e:
            print(f"[AI] Strategy error: {e}")
            return {
                "queries": professions if professions else ["работа"],
                "search_config": {
                    "city": city,
                    "salary_from": salary,
                    "employment_type": employment_type,
                    "company": company,
                    "strict_company": strict_company,
                }
            }

    async def _execute_search(
        self,
        strategy: dict,
        use_live_search: bool,
        use_feed_search: bool = True,
        exclude_vacancy_ids: Optional[list[str]] = None,
    ) -> list:
        """Выполнение поиска вакансий."""
        queries = strategy.get("queries", [])
        config = strategy.get("search_config", {})

        if not queries:
            return []

        city = config.get("city", "")
        salary_from = config.get("salary_from")
        employment_type = config.get("employment_type")  # remote, full, part
        exclude_ids = [str(v) for v in (exclude_vacancy_ids or [])]

        all_vacancies: list = []

        # Поиск в БД
        if use_feed_search:
            try:
                db_filters = SearchFilters(
                    queries=queries[:10],
                    city=city,
                    salary_from=salary_from,
                    employment_type=employment_type,
                    exclude_vacancy_ids=exclude_ids,
                    search_in_feed=True,
                    search_online=False,
                )
                db_result = await vacancy_search.search(db_filters)
                all_vacancies = list(db_result.vacancies)
                print(f"[AI] DB: {len(all_vacancies)} vacancies")
            except Exception as e:
                print(f"[AI] DB search error: {e}")
                all_vacancies = []
        else:
            print("[AI] DB search skipped by request")

        # Live поиск если нужен
        if use_live_search:
            try:
                live_filters = SearchFilters(
                    queries=queries[:5],
                    city=city,
                    salary_from=salary_from,
                    employment_type=employment_type,
                    exclude_vacancy_ids=exclude_ids,
                    search_in_feed=False,
                    search_online=True,
                )
                live_result = await vacancy_search.search(live_filters)

                # Дедупликация
                seen_ids = {v.id for v in all_vacancies}
                for v in live_result.vacancies:
                    if v.id not in seen_ids:
                        all_vacancies.append(v)
                        seen_ids.add(v.id)

                print(f"[AI] Live: +{len(live_result.vacancies)} vacancies")
            except Exception as e:
                print(f"[AI] Live search error: {e}")
        else:
            print("[AI] Live search skipped by request")

        # Дополнительная гарантия исключения ID для режима "найди ещё"
        if exclude_ids:
            exclude_set = set(exclude_ids)
            all_vacancies = [v for v in all_vacancies if str(getattr(v, "id", "")) not in exclude_set]

        return all_vacancies

    async def _execute_search_stream(
        self,
        strategy: dict,
        use_live_search: bool,
        params: dict = None,
        use_feed_search: bool = True,
        exclude_vacancy_ids: Optional[list[str]] = None,
        lifestyle_preferences: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        """
        Streaming поиск с поэтапной фильтрацией.

        Args:
            strategy: Стратегия поиска с queries
            use_live_search: Использовать live поиск
            params: Параметры поиска для валидации (professions, city, etc.)
            use_feed_search: Искать в сохраненной ленте (БД)
            exclude_vacancy_ids: ID вакансий, которые нужно исключить
            lifestyle_preferences: Предпочтения стиля работы

        Yields:
            dict: {"accepted": [...], "rejected": [...]}
        """
        queries = strategy.get("queries", [])
        config = strategy.get("search_config", {})

        if not queries:
            return

        city = config.get("city", "")
        salary_from = config.get("salary_from")
        employment_type = config.get("employment_type")  # remote, full, part
        exclude_set = {str(v) for v in (exclude_vacancy_ids or [])}
        emitted_ids = set(exclude_set)

        # 1) Сначала лента (БД), если включена
        if use_feed_search:
            try:
                from services.vacancy_storage import vacancy_storage_service

                all_db_vacancies = []
                for query in queries[:10]:
                    try:
                        stored, _ = await vacancy_storage_service.search_vacancies(
                            query=query,
                            city=city,
                            salary_from=salary_from,
                            employment_type=employment_type,
                            limit=50,
                            offset=0,
                        )
                        for sv in stored:
                            vacancy = vacancy_storage_service.to_vacancy(sv)
                            vid = self._vacancy_id(vacancy)
                            if vid and vid not in emitted_ids:
                                all_db_vacancies.append(vacancy)
                                emitted_ids.add(vid)
                    except Exception as e:
                        print(f"[AI] DB search error for '{query}': {e}")

                if all_db_vacancies:
                    print(f"[AI] DB: {len(all_db_vacancies)} vacancies found, streaming with validation...")

                    chunk_size = 10
                    for i in range(0, len(all_db_vacancies), chunk_size):
                        chunk = all_db_vacancies[i:i + chunk_size]
                        validated_chunk = await self._validate_chunk(chunk, params)
                        accepted_chunk, rejected_chunk = self._apply_lifestyle_preferences(
                            validated_chunk,
                            lifestyle_preferences,
                        )
                        if accepted_chunk or rejected_chunk:
                            print(
                                f"[AI] DB chunk {i//chunk_size + 1}: "
                                f"{len(chunk)} -> {len(accepted_chunk)} accepted, {len(rejected_chunk)} rejected"
                            )
                            yield {"accepted": accepted_chunk, "rejected": rejected_chunk}
                        else:
                            print(f"[AI] DB chunk {i//chunk_size + 1}: all filtered out")
                    print(f"[AI] DB: {len(all_db_vacancies)} vacancies processed")
            except Exception as e:
                print(f"[AI] DB search error: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("[AI] DB search skipped by request")

        # 2) Потом live поиск, если включен
        if use_live_search:
            try:
                from tools.parsers.hh import HHParser
                from tools.parsers.superjob import SuperJobParser
                import asyncio

                hh_parser = HHParser()
                sj_parser = SuperJobParser()
                queries_to_search = queries[:5]

                print(f"[AI] Starting live search with {len(queries_to_search)} queries")

                parallel_tasks = []
                for query in queries_to_search:
                    single_filter = SearchFilters(
                        query=query,
                        city=city,
                        salary_from=salary_from,
                        employment_type=employment_type,
                    )
                    parallel_tasks.append(hh_parser.search(single_filter, limit=20))
                    parallel_tasks.append(sj_parser.search(single_filter, limit=20))

                print(f"[AI] Running {len(parallel_tasks)} parallel search tasks")
                results = await asyncio.gather(*parallel_tasks, return_exceptions=True)

                all_vacancies = []
                for result in results:
                    if isinstance(result, list):
                        for vacancy in result:
                            vid = self._vacancy_id(vacancy)
                            if vid and vid not in emitted_ids:
                                all_vacancies.append(vacancy)
                                emitted_ids.add(vid)
                    elif isinstance(result, Exception):
                        print(f"[AI] Search task error: {result}")

                print(f"[AI] Live search complete: {len(all_vacancies)} vacancies found")

                if all_vacancies:
                    chunk_size = 10
                    for i in range(0, len(all_vacancies), chunk_size):
                        chunk = all_vacancies[i:i + chunk_size]
                        validated_chunk = await self._validate_chunk(chunk, params)
                        accepted_chunk, rejected_chunk = self._apply_lifestyle_preferences(
                            validated_chunk,
                            lifestyle_preferences,
                        )
                        if accepted_chunk or rejected_chunk:
                            print(
                                f"[AI] Live chunk {i//chunk_size + 1}: "
                                f"{len(chunk)} -> {len(accepted_chunk)} accepted, {len(rejected_chunk)} rejected"
                            )
                            yield {"accepted": accepted_chunk, "rejected": rejected_chunk}
                        else:
                            print(f"[AI] Live chunk {i//chunk_size + 1}: all filtered out")
            except Exception as e:
                print(f"[AI] Live search error: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("[AI] Live search skipped by request")

    async def _validate_chunk(self, chunk: list, params: dict) -> list:
        """
        Быстрая LLM-валидация чанка вакансий.
        Проверяет релевантность и возвращает только подходящие.
        """
        if not chunk or not params:
            return chunk

        company = params.get("company")
        strict_company = bool(params.get("strict_company") and company)
        if strict_company:
            chunk = self._filter_vacancies_by_company(chunk, str(company))
            if not chunk:
                return []

        professions = params.get("professions", [])

        # Если агент валидатора отключен - возвращаем все
        if not is_agent_enabled("crew_validator"):
            return chunk

        try:
            # Формируем описание вакансий для валидатора
            vacancy_summaries = []
            for v in chunk:
                title = getattr(v, 'title', '') or ''
                company = getattr(v, 'company', '') or ''
                city = getattr(v, 'city', '') or ''
                salary = getattr(v, 'salary', '') or ''
                vid = getattr(v, 'id', '') or ''
                vacancy_summaries.append(
                    f"ID:{vid} | {title} | {company} | {city} | {salary}"
                )

            vacancies_text = "\n".join(vacancy_summaries)
            city = params.get("city", "")
            salary_from = params.get("salary_from")

            user_prompt = f"""ПАРАМЕТРЫ ПОИСКА:
- Профессии: {', '.join(professions)}
- Город: {city or 'любой'}
- Зарплата от: {salary_from or 'любая'}

ВАКАНСИИ ДЛЯ ПРОВЕРКИ:
{vacancies_text}

Проверь каждую вакансию и верни JSON с релевантными."""

            result = await self.fast_llm.chat(
                system_prompt=VALIDATOR_SYSTEM_PROMPT,
                user_message=user_prompt,
                json_mode=True,
                max_tokens=2000,
                agent_id="crew_validator",
            )
            validation = self._parse_json(result)

            # Извлекаем ID прошедших валидацию
            validated_ids = set()
            for item in validation.get("validated", []):
                vid = str(item.get("id", ""))
                validated_ids.add(vid)

            # Фильтруем чанк
            validated = [v for v in chunk if str(getattr(v, 'id', '')) in validated_ids]

            # Если ничего не прошло - пробуем альтернативное сопоставление ID
            if not validated and validated_ids:
                stripped_validated = set()
                for vid in validated_ids:
                    stripped_validated.add(vid)
                    for prefix in ["hh_", "sj_", "avito_", "superjob_"]:
                        if vid.startswith(prefix):
                            stripped_validated.add(vid[len(prefix):])
                        else:
                            stripped_validated.add(f"{prefix}{vid}")

                validated = [
                    v for v in chunk
                    if str(getattr(v, 'id', '')) in stripped_validated
                    or str(getattr(v, 'id', '')).split('_', 1)[-1] in validated_ids
                ]

            # Если после фильтрации пусто - возвращаем оригинал (fallback)
            if not validated:
                print(f"[AI] Chunk validation: all filtered, returning original {len(chunk)}")
                return chunk

            return validated

        except Exception as e:
            print(f"[AI] Chunk validation error: {e}, returning original")
            return chunk

    async def _validate_results(self, vacancies: list, params: dict) -> list:
        """Валидация вакансий через LLM."""
        if not vacancies:
            return []

        company = params.get("company")
        strict_company = bool(params.get("strict_company") and company)
        if strict_company:
            vacancies = self._filter_vacancies_by_company(vacancies, str(company))
            if not vacancies:
                return []

        if not is_agent_enabled("crew_validator"):
            print(f"[AI] Validator DISABLED - returning all {len(vacancies)} vacancies")
            return vacancies

        # Для большого числа вакансий - берём первые 50
        to_validate = vacancies[:50]

        # Формируем краткое описание вакансий
        vacancy_summaries = []
        for v in to_validate:
            title = getattr(v, 'title', '') or ''
            company = getattr(v, 'company', '') or ''
            city = getattr(v, 'city', '') or ''
            salary = getattr(v, 'salary', '') or ''
            vid = getattr(v, 'id', '') or ''
            vacancy_summaries.append(
                f"ID:{vid} | {title} | {company} | {city} | {salary}"
            )

        vacancies_text = "\n".join(vacancy_summaries)

        professions = params.get("professions", [])
        city = params.get("city", "")
        salary_from = params.get("salary_from")

        user_prompt = f"""Параметры поиска:
- Профессии: {', '.join(professions)}
- Город: {city or 'любой'}
- Зарплата от: {salary_from or 'любая'}

Вакансии для проверки:
{vacancies_text}

Проверь каждую вакансию и верни JSON."""

        try:
            result = await self.fast_llm.chat(
                system_prompt=VALIDATOR_SYSTEM_PROMPT,
                user_message=user_prompt,
                json_mode=True,
                max_tokens=4000,
                agent_id="crew_validator",
            )
            validation = self._parse_json(result)

            # Извлекаем ID прошедших валидацию
            validated_ids = set()
            for item in validation.get("validated", []):
                vid = str(item.get("id", ""))
                validated_ids.add(vid)

            print(f"[AI] Validator returned {len(validated_ids)} accepted, {len(validation.get('rejected', []))} rejected")

            # Если валидатор ничего не вернул - возвращаем все
            if not validated_ids:
                print(f"[AI] Validator returned no IDs, returning all {len(vacancies)} vacancies")
                return vacancies

            # Фильтруем - пробуем несколько форматов ID
            vacancy_ids = {str(getattr(v, 'id', '')) for v in vacancies}
            print(f"[AI] Vacancy IDs sample: {list(vacancy_ids)[:3]}")
            print(f"[AI] Validated IDs sample: {list(validated_ids)[:3]}")

            validated = [v for v in vacancies if str(getattr(v, 'id', '')) in validated_ids]

            # Если ID не совпали (разный формат) - пробуем без префикса
            if not validated and validated_ids:
                # Пробуем сопоставить без префикса "hh_", "sj_", "avito_"
                stripped_validated = set()
                for vid in validated_ids:
                    stripped_validated.add(vid)
                    # Убираем префикс если есть
                    for prefix in ["hh_", "sj_", "avito_", "superjob_"]:
                        if vid.startswith(prefix):
                            stripped_validated.add(vid[len(prefix):])
                        else:
                            stripped_validated.add(f"{prefix}{vid}")

                validated = [
                    v for v in vacancies
                    if str(getattr(v, 'id', '')) in stripped_validated
                    or str(getattr(v, 'id', '')).split('_', 1)[-1] in validated_ids
                ]
                print(f"[AI] After prefix-aware matching: {len(validated)}")

            # Если после фильтрации осталось слишком мало - возвращаем все
            if len(validated) < 3 and len(vacancies) > 5:
                print(f"[AI] Too few validated ({len(validated)}), returning all {len(vacancies)}")
                return vacancies

            return validated

        except Exception as e:
            print(f"[AI] Validation error: {e}")
            # При ошибке - возвращаем все вакансии
            return vacancies

    def _vacancy_id(self, vacancy: Any) -> str:
        """Безопасно получить ID вакансии как строку."""
        if isinstance(vacancy, dict):
            return str(vacancy.get("id", "") or "")
        return str(getattr(vacancy, "id", "") or "")

    def _with_lifestyle_metadata(self, vacancy: Any, match: Any) -> Any:
        """Attach lifestyle metadata to vacancy object/dict."""
        payload = {
            "fit_status": match.fit_status,
            "fit_score": match.fit_score,
            "fit_confidence": match.fit_confidence,
            "matched_reasons": match.matched_reasons,
            "mismatch_reasons": match.mismatch_reasons,
        }

        if hasattr(vacancy, "model_copy"):
            return vacancy.model_copy(update=payload)

        if isinstance(vacancy, dict):
            updated = dict(vacancy)
            updated.update(payload)
            return updated

        # Fallback for plain objects
        for key, value in payload.items():
            try:
                setattr(vacancy, key, value)
            except Exception:
                pass
        return vacancy

    def _annotate_rejected_vacancy(self, vacancy: Any, reason: str) -> Any:
        """Mark vacancy as rejected with a single reason."""
        payload = {
            "fit_status": "reject",
            "fit_score": 0,
            "fit_confidence": 0.7,
            "matched_reasons": [],
            "mismatch_reasons": [reason],
        }

        if hasattr(vacancy, "model_copy"):
            return vacancy.model_copy(update=payload)

        if isinstance(vacancy, dict):
            updated = dict(vacancy)
            updated.update(payload)
            return updated

        for key, value in payload.items():
            try:
                setattr(vacancy, key, value)
            except Exception:
                pass
        return vacancy

    def _apply_lifestyle_preferences(
        self,
        vacancies: list,
        lifestyle_preferences: Optional[dict],
    ) -> tuple[list, list]:
        """
        Apply lifestyle filters and split vacancies into accepted/rejected.
        """
        if not vacancies:
            return [], []

        if not lifestyle_matcher.has_active_preferences(lifestyle_preferences):
            return vacancies, []

        strict_mode = bool((lifestyle_preferences or {}).get("strict_mode", False))
        accepted: list = []
        rejected: list = []

        for vacancy in vacancies:
            match = lifestyle_matcher.evaluate(vacancy, lifestyle_preferences)
            vacancy_with_meta = self._with_lifestyle_metadata(vacancy, match)

            if strict_mode:
                # Strict mode: keep only explicit full matches
                if match.fit_status == "fit":
                    accepted.append(vacancy_with_meta)
                else:
                    rejected.append(vacancy_with_meta)
            else:
                # Soft mode: reject only clear mismatch
                if match.fit_status == "reject":
                    rejected.append(vacancy_with_meta)
                else:
                    accepted.append(vacancy_with_meta)

        return accepted, rejected

    def _normalize_text(self, text: str) -> str:
        """Нормализация текста для сравнений."""
        return re.sub(r"[^a-zа-яё0-9]+", " ", str(text or "").lower()).strip()

    def _dedupe_queries(self, queries: list[str]) -> list[str]:
        """Удалить дубли и пустые запросы, сохранив порядок."""
        result = []
        seen = set()
        for raw_query in queries or []:
            query = " ".join(str(raw_query or "").split())
            if not query:
                continue
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(query)
        return result

    def _build_strict_company_queries(
        self,
        professions: list[str],
        company: str,
        city: str,
    ) -> list[str]:
        """Собрать запросы только с упоминанием конкретной компании."""
        clean_professions = self._dedupe_queries(professions)
        queries: list[str] = []

        if clean_professions:
            for profession in clean_professions[:3]:
                queries.append(f"{profession} {company}")
                queries.append(f"{company} {profession}")
        else:
            queries.extend([
                company,
                f"вакансии {company}",
                f"работа в {company}",
            ])

        if city:
            queries.append(f"{company} {city}")

        return self._dedupe_queries(queries)[:6]

    def _detect_company_from_message(self, message: str) -> Optional[str]:
        """Детерминированное распознавание популярных брендов в сообщении."""
        text = str(message or "").lower()
        candidates = [
            ("СДЭК", ["сдэк", "cdek", "sdek"]),
            ("Ozon", ["ozon", "озон"]),
            ("Wildberries", ["wildberries", "вайлдберриз", "wb", "вб"]),
            ("Яндекс", ["яндекс", "yandex"]),
            ("Boxberry", ["boxberry", "боксберри"]),
        ]

        for canonical, aliases in candidates:
            for alias in aliases:
                if re.search(fr"(?<![a-zа-яё0-9]){re.escape(alias)}(?![a-zа-яё0-9])", text):
                    return canonical
        return None

    def _normalize_analysis_output(self, analysis: dict, message: str) -> dict:
        """Привести выход analyst к устойчивому формату и добавить company-правила."""
        analysis = analysis or {}
        params = analysis.get("parameters", {})
        if not isinstance(params, dict):
            params = {}

        professions = params.get("professions", [])
        if isinstance(professions, str):
            professions = [professions]
        professions = [str(p).strip() for p in professions if str(p).strip()]
        params["professions"] = professions

        city = params.get("city")
        if isinstance(city, str):
            city = city.strip()
        params["city"] = city or None

        company = params.get("company")
        if isinstance(company, list):
            company = company[0] if company else None
        if isinstance(company, str):
            company = company.strip()
        if not company:
            company = self._detect_company_from_message(message)

        strict_company = params.get("strict_company")
        if isinstance(strict_company, str):
            strict_company = strict_company.lower() in {"true", "1", "yes", "да"}
        if strict_company is None:
            strict_company = bool(company)

        params["company"] = company or None
        params["strict_company"] = bool(strict_company and company)

        request_type = str(analysis.get("request_type", "CHAT")).upper()
        if request_type not in {"SEARCH", "CLARIFICATION", "CHAT", "PROFILE"}:
            request_type = "CHAT"

        message_norm = self._normalize_text(message)
        has_job_intent = any(keyword in message_norm for keyword in ("работ", "ваканс", "ищу", "найд", "подбер"))

        if company and params.get("city") and (has_job_intent or request_type == "CLARIFICATION"):
            request_type = "SEARCH"
            analysis["clarification_question"] = None

        analysis["request_type"] = request_type
        analysis["parameters"] = params
        return analysis

    def _get_company_aliases(self, company: str) -> set[str]:
        """Собрать алиасы компании для строгой фильтрации."""
        normalized = self._normalize_text(company)
        compact = normalized.replace(" ", "")
        aliases = {compact, normalized}

        for key, variants in self.COMPANY_ALIAS_MAP.items():
            if key in compact or compact in variants:
                aliases.update(variants)

        for token in normalized.split():
            if len(token) >= 3:
                aliases.add(token)

        return {alias for alias in aliases if alias}

    def _filter_vacancies_by_company(self, vacancies: list, company: str) -> list:
        """Оставить только вакансии, где явно упомянута нужная компания."""
        if not vacancies or not company:
            return vacancies

        aliases = self._get_company_aliases(company)
        filtered = []

        for vacancy in vacancies:
            company_text = self._normalize_text(getattr(vacancy, "company", ""))
            title_text = self._normalize_text(getattr(vacancy, "title", ""))
            description_text = self._normalize_text(getattr(vacancy, "description", ""))[:400]

            haystack = f"{company_text} {title_text} {description_text}"
            haystack_compact = haystack.replace(" ", "")

            if any(alias in haystack or alias in haystack_compact for alias in aliases):
                filtered.append(vacancy)

        print(f"[AI] Company filter '{company}': {len(vacancies)} -> {len(filtered)}")
        return filtered

    def _get_similar_companies(self, company: str) -> list[str]:
        """Вернуть похожие компании для fallback-предложения."""
        key = self._normalize_text(company).replace(" ", "")
        if key in self.SIMILAR_COMPANIES_MAP:
            return self.SIMILAR_COMPANIES_MAP[key]

        for map_key, value in self.SIMILAR_COMPANIES_MAP.items():
            if map_key in key or key in map_key:
                return value

        return ["Ozon", "Wildberries", "Яндекс Доставка"]

    def _build_no_company_results_message(self, params: dict) -> str:
        """Сообщение при отсутствии вакансий в конкретной компании."""
        company = params.get("company") or "указанной компании"
        city = params.get("city")
        similar = self._get_similar_companies(str(company))
        example = ", ".join(similar[:2]) if similar else "другие компании"

        if city:
            return (
                f"По запросу в компанию {company} в {city} сейчас не нашёл вакансий. "
                f"Хочешь, предложу похожие компании (например, {example})?"
            )

        return (
            f"По запросу в компанию {company} сейчас не нашёл вакансий. "
            f"Хочешь, предложу похожие компании (например, {example})?"
        )

    def _set_pending_similar_offer(self, params: dict) -> None:
        """Запомнить, что ждём ответ пользователя по похожим компаниям."""
        if not self.session:
            return
        self.session.set_context("awaiting_similar_offer", True)
        self.session.set_context("pending_company", params.get("company"))
        self.session.set_context("pending_city", params.get("city"))

    def _clear_pending_similar_offer(self) -> None:
        """Сбросить состояние ожидания ответа про похожие компании."""
        if not self.session:
            return
        self.session.set_context("awaiting_similar_offer", False)
        self.session.set_context("pending_company", None)
        self.session.set_context("pending_city", None)

    def _is_affirmative(self, message: str) -> bool:
        text = self._normalize_text(message)
        if not text:
            return False
        patterns = [
            r"\bда\b",
            r"\bага\b",
            r"\bок\b",
            r"\bдавай\b",
            r"\bпредлагай\b",
            r"\bконечно\b",
            r"\bхочу\b",
            r"\byes\b",
        ]
        return any(re.search(pattern, text) for pattern in patterns)

    def _is_negative(self, message: str) -> bool:
        text = self._normalize_text(message)
        if not text:
            return False
        patterns = [
            r"\bнет\b",
            r"\bне надо\b",
            r"\bне нужно\b",
            r"\bнеа\b",
            r"\bотмена\b",
            r"\bхватит\b",
            r"\bno\b",
        ]
        return any(re.search(pattern, text) for pattern in patterns)

    def _looks_like_new_search(self, message: str) -> bool:
        text = self._normalize_text(message)
        if not text:
            return False
        has_intent = any(keyword in text for keyword in ("работ", "ваканс", "ищу", "найд", "подбер"))
        has_company = bool(self._detect_company_from_message(message))
        return has_intent or has_company

    def _handle_pending_similar_offer_message(self, message: str) -> Optional[CrewResult]:
        """Обработать ответ пользователя после вопроса про похожие компании."""
        if not self.session:
            return None
        if not self.session.get_context("awaiting_similar_offer", False):
            return None

        if self._looks_like_new_search(message):
            self._clear_pending_similar_offer()
            return None

        if self._is_negative(message):
            self._clear_pending_similar_offer()
            return CrewResult(
                response_text="Понял. Тогда укажи другую компанию или профессию, и продолжим поиск.",
                vacancies=[],
                request_type="CLARIFICATION",
                show_vacancies=False,
            )

        if self._is_affirmative(message):
            company = self.session.get_context("pending_company") or "этой компании"
            options = self._get_similar_companies(str(company))
            self._clear_pending_similar_offer()
            return CrewResult(
                response_text=(
                    f"Ок, могу предложить похожие компании: {', '.join(options)}. "
                    "Напиши, какие из них посмотреть."
                ),
                vacancies=[],
                request_type="CLARIFICATION",
                show_vacancies=False,
            )

        return CrewResult(
            response_text="Напиши, пожалуйста, да или нет: предложить похожие компании?",
            vacancies=[],
            request_type="CLARIFICATION",
            show_vacancies=False,
        )

    async def _compose_response(
        self,
        request_type: str,
        context: dict,
        user_name: str = None,
    ) -> str:
        """Формирование ответа пользователю."""
        if not is_agent_enabled("crew_composer"):
            print("[AI] Composer DISABLED - using fallback response")
            if request_type == "CLARIFICATION":
                return context.get("question", "Уточни, что ищешь?")
            elif request_type == "SEARCH":
                count = context.get("validated_count", 0)
                return f"Нашёл {count} вакансий."
            return "Я рядом как карьерный помощник. Могу помочь с поиском вакансий, резюме и стратегией поиска. Что сейчас для тебя важнее?"

        # Формируем промпт в зависимости от типа
        parts = []

        if user_name:
            parts.append(f"Имя пользователя: {user_name}")

        if request_type == "CLARIFICATION":
            params = context.get("parameters", {})
            question = context.get("question", "")
            parts.append(f"ТИП: Уточняющий вопрос")
            parts.append(f"Известно: город={params.get('city', '?')}, профессия={params.get('professions', '?')}, зарплата={params.get('salary_from', '?')}")
            parts.append(f"Нужно спросить: {question}")

        elif request_type == "SEARCH":
            params = context.get("parameters", {})
            total = context.get("total_found", 0)
            validated = context.get("validated_count", 0)
            parts.append(f"ТИП: Результаты поиска")
            parts.append(f"Профессии: {', '.join(params.get('professions', []))}")
            parts.append(f"Город: {params.get('city', 'не указан')}")
            parts.append(f"Найдено: {validated} подходящих из {total} всего")

        elif request_type == "PROFILE":
            parts.append("ТИП: Вопрос о профиле")
            parts.append(f"Сообщение: {context.get('message', '')}")

        else:
            parts.append("ТИП: Обычный диалог")
            parts.append(f"Сообщение: {context.get('message', '')}")

        user_prompt = "\n".join(parts)

        try:
            result = await self.main_llm.chat(
                system_prompt=COMPOSER_SYSTEM_PROMPT,
                user_message=user_prompt,
                max_tokens=500,
                agent_id="crew_composer",
            )
            result = result.strip().strip('"').strip("'")
            return result
        except Exception as e:
            print(f"[AI] Composer error: {e}")
            # Fallback ответы
            if request_type == "CLARIFICATION":
                return context.get("question", "Уточни, пожалуйста, что именно ищешь?")
            elif request_type == "SEARCH":
                count = context.get("validated_count", 0)
                return f"Нашёл {count} вакансий."
            else:
                return "Я рядом как карьерный помощник. Могу помочь с поиском вакансий, резюме и стратегией поиска. Что сейчас для тебя важнее?"

    def _parse_json(self, text: str) -> dict:
        """Парсинг JSON из ответа LLM."""
        if isinstance(text, dict):
            return text

        text = str(text).strip()

        # Убираем markdown блоки кода
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        # Ищем JSON объект
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Пробуем весь текст
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}


    async def process_message_stream(
        self,
        message: str,
        conversation_history: list[dict] = None,
        use_live_search: bool = True,
        use_feed_search: bool = True,
        exclude_vacancy_ids: Optional[list[str]] = None,
        lifestyle_preferences: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        """
        Streaming обработка сообщения.

        Yields:
            dict: {'type': 'text'|'vacancies'|'done', 'content': ...}
        """
        history = conversation_history or []
        context_summary = ""
        if self.session:
            context_summary = self.session.get_context_summary()

        user_name = self.user_data.get("name") or self.user_data.get("first_name")

        pending_response = self._handle_pending_similar_offer_message(message)
        if pending_response:
            yield {'type': 'text', 'content': pending_response.response_text}
            yield {'type': 'done'}
            return

        # Шаг 1: Анализ запроса
        yield {'type': 'progress', 'message': 'Анализирую запрос...'}

        analysis = await self._analyze_request(message, history, context_summary)
        request_type = analysis.get("request_type", "CHAT")
        params = analysis.get("parameters", {}) or {}

        # CLARIFICATION - сразу возвращаем
        if request_type == "CLARIFICATION":
            question = analysis.get("clarification_question", "")
            response_text = await self._compose_response(
                request_type="CLARIFICATION",
                context={
                    "parameters": params,
                    "question": question,
                },
                user_name=user_name,
            )
            yield {'type': 'text', 'content': response_text}
            yield {'type': 'done'}
            return

        # CHAT/PROFILE - сразу возвращаем
        if request_type in ("CHAT", "PROFILE"):
            response_text = await self._compose_response(
                request_type=request_type,
                context={"message": message},
                user_name=user_name,
            )
            yield {'type': 'text', 'content': response_text}
            yield {'type': 'done'}
            return

        # SEARCH - streaming вакансий
        yield {'type': 'progress', 'message': 'Создаю стратегию поиска...'}
        strategy = await self._create_strategy(params)

        yield {'type': 'progress', 'message': 'Ищу вакансии...'}

        # Streaming поиск
        all_vacancies = []
        all_rejected_vacancies = []
        chunk_count = 0
        async for batch in self._execute_search_stream(
            strategy,
            use_live_search=use_live_search,
            params=params,
            use_feed_search=use_feed_search,
            exclude_vacancy_ids=exclude_vacancy_ids,
            lifestyle_preferences=lifestyle_preferences,
        ):
            accepted_chunk = batch.get("accepted", [])
            rejected_chunk = batch.get("rejected", [])
            all_vacancies.extend(accepted_chunk)
            all_rejected_vacancies.extend(rejected_chunk)
            chunk_count += 1
            print(
                f"[AI] Sending chunk {chunk_count}: "
                f"{len(accepted_chunk)} accepted, {len(rejected_chunk)} rejected"
            )
            if accepted_chunk:
                yield {'type': 'vacancies_chunk', 'content': accepted_chunk}
            if rejected_chunk:
                yield {'type': 'rejected_vacancies', 'content': rejected_chunk}

        print(
            f"[AI] Total chunks sent: {chunk_count}, total vacancies: {len(all_vacancies)}, "
            f"rejected: {len(all_rejected_vacancies)}"
        )

        strict_company = bool(params.get("strict_company") and params.get("company"))
        if strict_company and not all_vacancies:
            self._set_pending_similar_offer(params)
            yield {'type': 'text', 'content': self._build_no_company_results_message(params)}
            yield {'type': 'done'}
            return

        self._clear_pending_similar_offer()

        # Чанки уже валидированы при отправке, формируем финальный ответ
        response_text = await self._compose_response(
            request_type="SEARCH",
            context={
                "parameters": params,
                "total_found": len(all_vacancies),
                "validated_count": len(all_vacancies),
            },
            user_name=user_name,
        )

        yield {'type': 'text', 'content': response_text}
        yield {'type': 'done'}

        # Обновляем сессию
        if self.session:
            if params.get("city"):
                self.session.update_preferences(city=params["city"])
            if params.get("professions"):
                self.session.update_preferences(query=", ".join(params["professions"]))
            if params.get("salary_from"):
                self.session.update_preferences(salary_from=params["salary_from"])
            if params.get("employment_type"):
                self.session.update_preferences(employment_type=params["employment_type"])


async def process_chat_message(
    user_id: str,
    message: str,
    chat_id: str = None,
    user_data: dict = None,
    use_live_search: bool = True,
    use_feed_search: bool = True,
    exclude_vacancy_ids: Optional[list[str]] = None,
    lifestyle_preferences: Optional[dict] = None,
) -> CrewResult:
    """
    Удобная функция для обработки сообщения.
    """
    session = session_manager.get_or_create_session(user_id, chat_id)
    crew = JobSearchCrew(user_id, user_data, session)
    result = await crew.process_message(
        message=message,
        conversation_history=session.get_recent_history(),
        use_live_search=use_live_search,
        use_feed_search=use_feed_search,
        exclude_vacancy_ids=exclude_vacancy_ids,
        lifestyle_preferences=lifestyle_preferences,
    )
    session.add_message("user", message)
    session.add_message("assistant", result.response_text)
    return result
