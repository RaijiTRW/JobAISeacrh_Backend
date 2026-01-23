"""
JobSearchCrew - главный модуль CrewAI для поиска работы.
"""
import json
import asyncio
from typing import Optional
from dataclasses import dataclass

from crewai import Crew, Process

from .agents import (
    create_career_advisor,
    create_requirements_analyst,
    create_search_strategist,
    create_vacancy_validator,
    create_response_composer,
)
from .tasks import (
    create_analyze_request_task,
    create_strategy_task,
    create_execute_search_task,
    create_validate_results_task,
    create_compose_response_task,
)
from .tools.search_tools import vacancy_search_tool, database_search_tool
from .tools.profile_tools import user_profile_tool
from .session_manager import Session, SessionManager


@dataclass
class CrewResult:
    """Результат работы Crew."""
    response_text: str
    vacancies: list[dict]
    request_type: str
    show_vacancies: bool = True
    suggested_actions: list[str] = None


class JobSearchCrew:
    """
    Главный класс для управления CrewAI агентами поиска работы.

    Использование:
        crew = JobSearchCrew(user_id, user_data, session)
        result = await crew.process_message("Найди работу в Москве")
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

        # Настраиваем tools с user_id
        user_profile_tool.set_user_id(user_id)

        # Создаём агентов
        self.career_advisor = create_career_advisor()
        self.requirements_analyst = create_requirements_analyst()
        self.search_strategist = create_search_strategist()
        self.vacancy_validator = create_vacancy_validator()
        self.response_composer = create_response_composer()

        # Добавляем tools агенту-стратегу
        self.search_strategist.tools = [vacancy_search_tool, database_search_tool]

    async def process_message(
        self,
        message: str,
        conversation_history: list[dict] = None,
        use_live_search: bool = True,
    ) -> CrewResult:
        """
        Обработать сообщение пользователя.

        Args:
            message: Сообщение пользователя
            conversation_history: История диалога
            use_live_search: Использовать ли live поиск (hh, avito)

        Returns:
            CrewResult с ответом и вакансиями
        """
        history = conversation_history or []
        context_summary = ""

        if self.session:
            context_summary = self.session.get_context_summary()

        # Извлекаем имя пользователя
        user_name = self.user_data.get("name") or self.user_data.get("first_name")

        # Шаг 1: Анализ запроса
        print(f"[CrewAI] Step 1: Analyzing request...")
        analysis_result = await self._analyze_request(message, history, context_summary)

        request_type = analysis_result.get("request_type", "CHAT")
        print(f"[CrewAI] Request type: {request_type}")

        # Шаг 2: Обработка в зависимости от типа
        if request_type == "CLARIFICATION":
            # Нужны уточнения - формируем вопрос
            response = await self._compose_response(
                request_type="CLARIFICATION",
                analysis_result=analysis_result,
                user_name=user_name,
            )
            return CrewResult(
                response_text=response.get("response_text", ""),
                vacancies=[],
                request_type=request_type,
                show_vacancies=False,
            )

        elif request_type == "SEARCH":
            # Полный поиск
            print(f"[CrewAI] Step 2: Creating search strategy...")
            strategy = await self._create_strategy(analysis_result)

            print(f"[CrewAI] Step 3: Executing search...")
            search_results = await self._execute_search(strategy, use_live_search)

            vacancies = search_results.get("vacancies", [])
            print(f"[CrewAI] Found {len(vacancies)} vacancies")

            # Шаг 4: Валидация (если есть результаты)
            validation_results = {"stats": {"total": 0, "accepted": 0}}
            validated_vacancies = []

            if vacancies:
                print(f"[CrewAI] Step 4: Validating results...")
                validation_results = await self._validate_results(vacancies, analysis_result)

                # Фильтруем вакансии по результатам валидации
                validated_ids = {v["id"] for v in validation_results.get("validated", [])}
                validated_vacancies = [v for v in vacancies if v.get("id") in validated_ids]

                # Сортируем по рейтингу
                rating_order = {"A": 0, "B": 1, "C": 2}
                validated_map = {v["id"]: v.get("rating", "C") for v in validation_results.get("validated", [])}

                validated_vacancies.sort(
                    key=lambda x: rating_order.get(validated_map.get(x.get("id"), "C"), 2)
                )

            # Шаг 5: Формирование ответа
            print(f"[CrewAI] Step 5: Composing response...")
            response = await self._compose_response(
                request_type="SEARCH",
                analysis_result=analysis_result,
                search_results=search_results,
                validation_results=validation_results,
                user_name=user_name,
            )

            # Обновляем сессию если есть
            if self.session:
                params = analysis_result.get("parameters", {})
                if params.get("city"):
                    self.session.update_preferences(city=params["city"])
                if params.get("professions"):
                    self.session.update_preferences(professions=params["professions"])
                if params.get("salary_from"):
                    self.session.update_preferences(salary_from=params["salary_from"])

            return CrewResult(
                response_text=response.get("response_text", ""),
                vacancies=validated_vacancies,
                request_type=request_type,
                show_vacancies=response.get("show_vacancies", True),
                suggested_actions=response.get("suggested_actions"),
            )

        else:
            # CHAT или PROFILE - просто отвечаем
            response = await self._compose_response(
                request_type=request_type,
                analysis_result=analysis_result,
                user_name=user_name,
            )
            return CrewResult(
                response_text=response.get("response_text", ""),
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
        """Анализ запроса пользователя."""
        task = create_analyze_request_task(
            agent=self.requirements_analyst,
            user_message=message,
            user_profile=self.user_data,
            conversation_history=history,
            context_summary=context_summary,
        )

        crew = Crew(
            agents=[self.requirements_analyst],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )

        result = await asyncio.to_thread(crew.kickoff)
        return self._parse_json_result(result.raw if hasattr(result, 'raw') else str(result))

    async def _create_strategy(self, analysis_result: dict) -> dict:
        """Создание стратегии поиска."""
        task = create_strategy_task(
            agent=self.search_strategist,
            analysis_result=analysis_result,
        )

        crew = Crew(
            agents=[self.search_strategist],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )

        result = await asyncio.to_thread(crew.kickoff)
        return self._parse_json_result(result.raw if hasattr(result, 'raw') else str(result))

    async def _execute_search(self, strategy: dict, use_live_search: bool) -> dict:
        """Выполнение поиска вакансий."""
        # Для поиска используем tools напрямую для лучшего контроля
        search_queries = strategy.get("search_queries", [])
        config = strategy.get("search_config", {})

        all_queries = []
        for sq in search_queries:
            all_queries.extend(sq.get("queries", []))

        if not all_queries:
            return {"vacancies": [], "stats": {"total_found": 0}}

        city = config.get("city", "")
        salary_from = config.get("salary_from")

        # Сначала ищем в базе
        try:
            db_result = database_search_tool._run(
                queries=all_queries[:10],  # Ограничиваем количество
                city=city,
                salary_from=salary_from,
            )
            db_vacancies = db_result if isinstance(db_result, list) else []
        except Exception as e:
            print(f"[CrewAI] Database search error: {e}")
            db_vacancies = []

        # Live поиск если нужен
        live_vacancies = []
        if use_live_search and len(db_vacancies) < 10:
            try:
                live_result = vacancy_search_tool._run(
                    queries=all_queries[:5],
                    city=city,
                    salary_from=salary_from,
                    include_live=True,
                )
                live_vacancies = live_result if isinstance(live_result, list) else []
            except Exception as e:
                print(f"[CrewAI] Live search error: {e}")

        # Объединяем и удаляем дубликаты
        all_vacancies = db_vacancies + live_vacancies
        seen_ids = set()
        unique_vacancies = []
        for v in all_vacancies:
            vid = v.get("id") or v.get("url", "")
            if vid not in seen_ids:
                seen_ids.add(vid)
                unique_vacancies.append(v)

        return {
            "vacancies": unique_vacancies,
            "stats": {
                "total_found": len(unique_vacancies),
                "from_database": len(db_vacancies),
                "from_live": len(live_vacancies),
            }
        }

    async def _validate_results(
        self,
        vacancies: list[dict],
        analysis_result: dict,
    ) -> dict:
        """Валидация вакансий."""
        task = create_validate_results_task(
            agent=self.vacancy_validator,
            vacancies=vacancies,
            original_request=analysis_result,
        )

        crew = Crew(
            agents=[self.vacancy_validator],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )

        result = await asyncio.to_thread(crew.kickoff)
        return self._parse_json_result(result.raw if hasattr(result, 'raw') else str(result))

    async def _compose_response(
        self,
        request_type: str,
        analysis_result: dict = None,
        search_results: dict = None,
        validation_results: dict = None,
        user_name: str = None,
    ) -> dict:
        """Формирование ответа пользователю."""
        task = create_compose_response_task(
            agent=self.response_composer,
            request_type=request_type,
            analysis_result=analysis_result,
            search_results=search_results,
            validation_results=validation_results,
            user_name=user_name,
        )

        crew = Crew(
            agents=[self.response_composer],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )

        result = await asyncio.to_thread(crew.kickoff)
        return self._parse_json_result(result.raw if hasattr(result, 'raw') else str(result))

    def _parse_json_result(self, result: str) -> dict:
        """Парсинг JSON результата от агента."""
        if isinstance(result, dict):
            return result

        # Пробуем найти JSON в тексте
        text = str(result)

        # Ищем JSON блок
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)

        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Возвращаем как текст
        return {"response_text": text, "request_type": "CHAT"}


# Глобальный менеджер сессий
session_manager = SessionManager()


async def process_chat_message(
    user_id: str,
    message: str,
    chat_id: str = None,
    user_data: dict = None,
    use_live_search: bool = True,
) -> CrewResult:
    """
    Удобная функция для обработки сообщения чата.

    Args:
        user_id: ID пользователя
        message: Сообщение
        chat_id: ID чата (опционально)
        user_data: Данные пользователя (опционально)
        use_live_search: Использовать live поиск

    Returns:
        CrewResult с ответом
    """
    # Получаем или создаём сессию
    session = session_manager.get_or_create_session(user_id, chat_id)

    # Создаём Crew и обрабатываем
    crew = JobSearchCrew(user_id, user_data, session)
    result = await crew.process_message(
        message=message,
        conversation_history=session.get_recent_history(),
        use_live_search=use_live_search,
    )

    # Сохраняем в историю
    session.add_message("user", message)
    session.add_message("assistant", result.response_text)

    return result
