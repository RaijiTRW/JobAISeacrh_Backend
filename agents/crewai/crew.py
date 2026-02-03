"""
JobSearchCrew - мультиагентная система поиска работы.
Реализация без зависимости от crewai/langchain - прямые вызовы OpenRouter API.
"""
import json
import re
from typing import Optional, AsyncIterator
from dataclasses import dataclass, field

from .llm_config import get_main_llm, get_fast_llm
from .session_manager import Session, SessionManager, session_manager
from agents.agents_config import is_agent_enabled

# Импорты для поиска
from tools.search import vacancy_search
from models.vacancy import SearchFilters
from services.user_profile import user_profile_service


@dataclass
class CrewResult:
    """Результат работы Crew."""
    response_text: str
    vacancies: list = field(default_factory=list)
    request_type: str = "CHAT"
    show_vacancies: bool = True
    suggested_actions: list = field(default_factory=list)


# === СИСТЕМНЫЕ ПРОМПТЫ АГЕНТОВ ===

ANALYST_SYSTEM_PROMPT = """Ты - аналитик запросов для поиска работы. Твоя задача - понять что хочет пользователь и извлечь параметры поиска.

ПРАВИЛА:
1. Анализируй сообщение пользователя и извлеки параметры
2. Если запрос размытый (нет профессии или города) - задай ОДИН уточняющий вопрос
3. Учитывай контекст предыдущих сообщений
4. Определи тип запроса: SEARCH, CLARIFICATION, CHAT, PROFILE

ПАРАМЕТРЫ ДЛЯ ИЗВЛЕЧЕНИЯ:
- professions: список профессий (может быть несколько!)
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
- Задавай ОДИН вопрос, не несколько сразу

ПРИМЕРЫ:
"Найди работу в Москве от 80к" → CLARIFICATION (нет профессии)
"Продавец в Краснодаре" → SEARCH (всё ясно)
"Ищу работу продавцом или администратором в Новороссийске от 40к" → SEARCH (несколько профессий)
"Привет, как дела?" → CHAT
"Что у меня в резюме?" → PROFILE

ФОРМАТ ОТВЕТА (строго JSON):
{
    "request_type": "SEARCH" | "CLARIFICATION" | "CHAT" | "PROFILE",
    "parameters": {
        "professions": ["профессия1", "профессия2"],
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
        "salary_to": число | null
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

COMPOSER_SYSTEM_PROMPT = """Ты - составитель ответов для AI-помощника по поиску работы. Формируешь дружелюбные, краткие ответы.

ПРАВИЛА:
1. КРАТКОСТЬ: 1-3 предложения максимум
2. КОНКРЕТИКА: "Нашёл 23 вакансии" а не "нашёл несколько"
3. ТОН: дружелюбный, но профессиональный
4. БЕЗ канцеляризмов и официоза
5. НЕ перечисляй вакансии в тексте - они показываются отдельно карточками

ПРИ УТОЧНЕНИИ:
- Подтверди что понял
- Задай ОДИН вопрос
- Пример: "Понял, ищем в Москве от 80 тысяч. А какая профессия интересует?"

ПРИ ПОИСКЕ:
- Краткое резюме: "Нашёл X вакансий [профессия] в [город]."
- Если мало (<5): предложи расширить, НЕ извиняйся

ПРИ ЧАТЕ:
- Ответь дружелюбно
- Направь к поиску если уместно

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
    ) -> CrewResult:
        """Обработать сообщение пользователя."""
        history = conversation_history or []
        context_summary = ""
        if self.session:
            context_summary = self.session.get_context_summary()

        user_name = self.user_data.get("name") or self.user_data.get("first_name")

        # Шаг 1: Анализ запроса
        print(f"[AI] Step 1: Analyzing request...")
        analysis = await self._analyze_request(message, history, context_summary)

        request_type = analysis.get("request_type", "CHAT")
        print(f"[AI] Request type: {request_type}")

        # Шаг 2: Обработка по типу
        if request_type == "CLARIFICATION":
            question = analysis.get("clarification_question", "")
            response_text = await self._compose_response(
                request_type="CLARIFICATION",
                context={
                    "parameters": analysis.get("parameters", {}),
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
            params = analysis.get("parameters", {})

            # Шаг 2: Генерация поисковых запросов
            print(f"[AI] Step 2: Creating search strategy...")
            strategy = await self._create_strategy(params)

            # Шаг 3: Выполнение поиска
            print(f"[AI] Step 3: Executing search...")
            vacancies = await self._execute_search(strategy, use_live_search)
            print(f"[AI] Found {len(vacancies)} vacancies")

            # Шаг 4: Валидация
            validated_vacancies = vacancies
            if vacancies and len(vacancies) > 0:
                print(f"[AI] Step 4: Validating results...")
                validated_vacancies = await self._validate_results(vacancies, params)
                print(f"[AI] Validated: {len(validated_vacancies)} vacancies")

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

            return CrewResult(
                response_text=response_text,
                vacancies=validated_vacancies,
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
                return {"request_type": "SEARCH", "parameters": {"professions": [message], "city": ""}}

            result = await self.main_llm.chat(
                system_prompt=ANALYST_SYSTEM_PROMPT,
                user_message=user_prompt,
                json_mode=True,
                agent_id="crew_analyst",
            )
            return self._parse_json(result)
        except Exception as e:
            print(f"[AI] Analyst error: {e}")
            return {"request_type": "CHAT", "parameters": {}}

    async def _create_strategy(self, params: dict) -> dict:
        """Генерация поисковых запросов."""
        professions = params.get("professions", [])
        city = params.get("city", "")
        salary = params.get("salary_from")

        user_prompt = f"""Параметры поиска:
- Профессии: {', '.join(professions) if professions else 'не указана'}
- Город: {city or 'не указан'}
- Зарплата от: {salary or 'не указана'}

Сгенерируй поисковые запросы (JSON)."""

        try:
            if not is_agent_enabled("crew_strategist"):
                print("[AI] Strategist DISABLED - using professions directly")
                return {
                    "queries": professions if professions else ["работа"],
                    "search_config": {"city": city, "salary_from": salary},
                }

            result = await self.fast_llm.chat(
                system_prompt=STRATEGIST_SYSTEM_PROMPT,
                user_message=user_prompt,
                json_mode=True,
                agent_id="crew_strategist",
            )
            return self._parse_json(result)
        except Exception as e:
            print(f"[AI] Strategy error: {e}")
            return {
                "queries": professions if professions else ["работа"],
                "search_config": {
                    "city": city,
                    "salary_from": salary,
                }
            }

    async def _execute_search(self, strategy: dict, use_live_search: bool) -> list:
        """Выполнение поиска вакансий."""
        queries = strategy.get("queries", [])
        config = strategy.get("search_config", {})

        if not queries:
            return []

        city = config.get("city", "")
        salary_from = config.get("salary_from")

        # Поиск в БД
        try:
            db_filters = SearchFilters(
                queries=queries[:10],
                city=city,
                salary_from=salary_from,
                search_in_feed=True,
                search_online=False,
            )
            db_result = await vacancy_search.search(db_filters)
            all_vacancies = list(db_result.vacancies)
            print(f"[AI] DB: {len(all_vacancies)} vacancies")
        except Exception as e:
            print(f"[AI] DB search error: {e}")
            all_vacancies = []

        # Live поиск если нужен
        if use_live_search:
            try:
                live_filters = SearchFilters(
                    queries=queries[:5],
                    city=city,
                    salary_from=salary_from,
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

        return all_vacancies

    async def _execute_search_stream(
        self,
        strategy: dict,
        use_live_search: bool
    ) -> AsyncIterator[list]:
        """
        Streaming поиск - yield вакансии по мере нахождения.

        Yields:
            list: Чанк вакансий
        """
        queries = strategy.get("queries", [])
        config = strategy.get("search_config", {})

        if not queries:
            return

        city = config.get("city", "")
        salary_from = config.get("salary_from")

        # 1. Сначала ищем в БД быстро
        try:
            db_filters = SearchFilters(
                queries=queries[:10],
                city=city,
                salary_from=salary_from,
                search_in_feed=True,
                search_online=False,
            )
            db_result = await vacancy_search.search(db_filters)

            if db_result.vacancies:
                yield list(db_result.vacancies)  # Первые результаты из БД
                print(f"[AI] DB: {len(db_result.vacancies)} vacancies sent")
        except Exception as e:
            print(f"[AI] DB search error: {e}")

        # 2. Потом live поиск - отправляем по мере получения от каждого источника
        if use_live_search:
            try:
                live_filters = SearchFilters(
                    queries=queries[:5],
                    city=city,
                    salary_from=salary_from,
                    search_in_feed=False,
                    search_online=True,
                )
                live_result = await vacancy_search.search(live_filters)

                if live_result.vacancies:
                    vacancies_list = list(live_result.vacancies)
                    yield vacancies_list  # Live результаты
                    print(f"[AI] Live: {len(vacancies_list)} vacancies sent")
                else:
                    print(f"[AI] Live: no vacancies found")
            except Exception as e:
                print(f"[AI] Live search error: {e}")
                import traceback
                traceback.print_exc()

    async def _validate_results(self, vacancies: list, params: dict) -> list:
        """Валидация вакансий через LLM."""
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
            return "Привет! Напиши профессию и город для поиска."

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
                return "Привет! Я помогу найти работу. Напиши профессию и город."

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

        # Шаг 1: Анализ запроса
        yield {'type': 'progress', 'message': 'Анализирую запрос...'}

        analysis = await self._analyze_request(message, history, context_summary)
        request_type = analysis.get("request_type", "CHAT")

        # CLARIFICATION - сразу возвращаем
        if request_type == "CLARIFICATION":
            question = analysis.get("clarification_question", "")
            response_text = await self._compose_response(
                request_type="CLARIFICATION",
                context={
                    "parameters": analysis.get("parameters", {}),
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
        params = analysis.get("parameters", {})

        yield {'type': 'progress', 'message': 'Создаю стратегию поиска...'}
        strategy = await self._create_strategy(params)

        yield {'type': 'progress', 'message': 'Ищу вакансии...'}

        # Streaming поиск
        all_vacancies = []
        chunk_count = 0
        async for vacancy_chunk in self._execute_search_stream(strategy, use_live_search):
            all_vacancies.extend(vacancy_chunk)
            chunk_count += 1
            print(f"[AI] Sending chunk {chunk_count} with {len(vacancy_chunk)} vacancies")
            yield {'type': 'vacancies_chunk', 'content': vacancy_chunk}

        print(f"[AI] Total chunks sent: {chunk_count}, total vacancies: {len(all_vacancies)}")

        # Валидация (опционально, можно пропускать для скорости)
        validated_vacancies = all_vacancies
        if all_vacancies:
            print(f"[AI] Starting validation of {len(all_vacancies)} vacancies...")
            yield {'type': 'progress', 'message': 'Проверяю результаты...'}
            validated_vacancies = await self._validate_results(all_vacancies, params)
            print(f"[AI] Validation complete: {len(validated_vacancies)} vacancies accepted")
        else:
            print(f"[AI] No vacancies to validate")

        # Формируем финальный ответ
        response_text = await self._compose_response(
            request_type="SEARCH",
            context={
                "parameters": params,
                "total_found": len(all_vacancies),
                "validated_count": len(validated_vacancies),
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


async def process_chat_message(
    user_id: str,
    message: str,
    chat_id: str = None,
    user_data: dict = None,
    use_live_search: bool = True,
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
    )
    session.add_message("user", message)
    session.add_message("assistant", result.response_text)
    return result
