"""
Scheduled jobs для парсинга и проверки вакансий
"""

import asyncio
import httpx
from datetime import datetime
from typing import Optional

from config import get_settings
from models.vacancy import SearchFilters, Vacancy
from tools.parsers.hh import HHParser
from tools.parsers.avito import AvitoParser
from tools.parsers.superjob import SuperJobParser
from services.vacancy_storage import vacancy_storage_service, StoredVacancy
from scheduler.human_behavior import (
    HumanSession,
    human_delay,
    session_break,
    is_working_hours,
    get_batch_size,
    get_random_query,
    get_random_city,
    get_random_queries,
    get_random_cities,
    get_random_user_agent,
    POPULAR_QUERIES,
    POPULAR_CITIES,
    # Новые функции для массового парсинга
    generate_session_requests,
    get_mass_batch_size,
    smart_delay,
    micro_break,
)


# Военные ключевые слова для фильтрации
MILITARY_KEYWORDS = [
    "военн",  # военный, военнослужащий, военная
    "бпла",
    "беспилотн",
    "дрон",
    "военкомат",
    "контракт сво",
    "участни",  # участник СВО
    "мобилизац",
    "армия",
    "военная служба",
    "военная часть",
    "военное",
    "войск",  # войсковая часть
    "обороны",  # министерство обороны
    "казарм",
]


def is_military_vacancy(vacancy: Vacancy) -> bool:
    """Проверка на военную тематику"""
    full_text = f"{(vacancy.title or '').lower()} {(vacancy.description or '').lower()}"
    return any(keyword in full_text for keyword in MILITARY_KEYWORDS)


class ParsingJob:
    """
    Job для парсинга вакансий с HH и SuperJob.
    Запускается каждые 2 часа.
    Avito парсится отдельно каждые 4 часа.
    """

    def __init__(self):
        self.settings = get_settings()
        self.hh_parser = HHParser()
        self.sj_parser = SuperJobParser()
        self.is_running = False

    async def run(self) -> dict:
        """
        Запустить парсинг HH и SuperJob.
        Возвращает статистику.
        """
        if self.is_running:
            print("[ParsingJob] Already running, skipping...")
            return {"status": "skipped", "reason": "already_running"}

        self.is_running = True
        start_time = datetime.now()
        stats = {
            "started_at": start_time.isoformat(),
            "hh": {"parsed": 0, "saved": 0},
            "superjob": {"parsed": 0, "saved": 0},
            "total_saved": 0,
            "errors": [],
        }

        print(f"[ParsingJob] Starting HH/SuperJob at {start_time}")

        try:
            # Парсим по случайным запросам и городам (для разнообразия)
            queries_to_parse = get_random_queries(12)  # 12 случайных запросов
            cities_to_parse = get_random_cities(8)  # 8 городов (Москва + СПб + 6 случайных)
            print(f"[ParsingJob] Queries: {queries_to_parse}")
            print(f"[ParsingJob] Cities: {cities_to_parse}")

            for query in queries_to_parse:
                for city in cities_to_parse:
                    print(f"[ParsingJob] Parsing: {query} in {city}")

                    # HH (самый толерантный)
                    try:
                        hh_stats = await self._parse_source(
                            "hh", self.hh_parser, query, city
                        )
                        stats["hh"]["parsed"] += hh_stats["parsed"]
                        stats["hh"]["saved"] += hh_stats["saved"]
                    except Exception as e:
                        stats["errors"].append(f"HH: {str(e)}")
                        print(f"[ParsingJob] HH error: {e}")

                    # SuperJob
                    try:
                        sj_stats = await self._parse_source(
                            "superjob", self.sj_parser, query, city
                        )
                        stats["superjob"]["parsed"] += sj_stats["parsed"]
                        stats["superjob"]["saved"] += sj_stats["saved"]
                    except Exception as e:
                        stats["errors"].append(f"SuperJob: {str(e)}")
                        print(f"[ParsingJob] SuperJob error: {e}")

                    # Перерыв между городами
                    await human_delay(min_seconds=5, max_seconds=15)

                # Перерыв между запросами
                await session_break()

            stats["total_saved"] = stats["hh"]["saved"] + stats["superjob"]["saved"]

        except Exception as e:
            stats["errors"].append(f"General: {str(e)}")
            print(f"[ParsingJob] General error: {e}")

        finally:
            self.is_running = False
            end_time = datetime.now()
            stats["ended_at"] = end_time.isoformat()
            stats["duration_seconds"] = (end_time - start_time).total_seconds()

        print(f"[ParsingJob] Finished. Stats: {stats}")
        return stats

    async def _parse_source(
        self,
        source: str,
        parser,
        query: str,
        city: str,
    ) -> dict:
        """Парсинг одного источника"""
        session = HumanSession(source)
        stats = {"parsed": 0, "saved": 0}

        try:
            await session.before_request()

            filters = SearchFilters(query=query, city=city)
            batch_size = get_batch_size(source)

            vacancies = await parser.search(filters, limit=batch_size)
            stats["parsed"] = len(vacancies)

            # Фильтруем военную тематику и сохраняем в БД
            for vacancy in vacancies:
                if is_military_vacancy(vacancy):
                    print(f"[ParsingJob] Skipped military vacancy: {vacancy.title}")
                    continue
                if await vacancy_storage_service.save_vacancy(vacancy):
                    stats["saved"] += 1

            print(f"[ParsingJob] {source}: parsed={stats['parsed']}, saved={stats['saved']}")

        except Exception as e:
            print(f"[ParsingJob] {source} parse error: {e}")

        return stats

class MassParsingJob:
    """
    Job для массового сбора вакансий с HH и SuperJob.
    Запускается каждые 2 часа.
    Цель: ~10,000 вакансий за сеанс.
    """

    def __init__(self):
        self.settings = get_settings()
        self.hh_parser = HHParser()
        self.sj_parser = SuperJobParser()
        self.is_running = False
        self.user_agent = get_random_user_agent()

    async def run(self) -> dict:
        """
        Запустить массовый парсинг HH и SuperJob.
        50 запросов × 2 источника = 100 API вызовов.
        """
        if self.is_running:
            print("[MassParsingJob] Already running, skipping...")
            return {"status": "skipped", "reason": "already_running"}

        self.is_running = True
        start_time = datetime.now()
        stats = {
            "started_at": start_time.isoformat(),
            "hh": {"parsed": 0, "saved": 0, "requests": 0},
            "superjob": {"parsed": 0, "saved": 0, "requests": 0},
            "total_saved": 0,
            "unique_cities": [],
            "unique_queries": [],
            "errors": [],
        }

        print(f"[MassParsingJob] Starting at {start_time}")

        try:
            # Генерируем 50 уникальных комбинаций query+city
            requests_count = self.settings.mass_parsing_requests
            session_requests = generate_session_requests(requests_count)

            cities_seen = set()
            queries_seen = set()

            print(f"[MassParsingJob] Generated {len(session_requests)} request combinations")

            # Получаем событие отмены для проверки
            from scheduler.scheduler import get_job_cancel_event
            cancel_event = get_job_cancel_event("mass_parsing_job")

            for i, req in enumerate(session_requests):
                # Проверяем отмену перед каждой итерацией
                if cancel_event.is_set():
                    print(f"[MassParsingJob] Cancel requested at iteration {i+1}, stopping...")
                    stats["status"] = "cancelled"
                    break

                query = req["query"]
                city = req["city"]

                cities_seen.add(city)
                queries_seen.add(query)

                print(f"[MassParsingJob] [{i+1}/{len(session_requests)}] {query} in {city}")

                # HH (увеличенный batch до 100)
                try:
                    hh_batch = get_mass_batch_size("hh")
                    hh_stats = await self._parse_source("hh", self.hh_parser, query, city, hh_batch)
                    stats["hh"]["parsed"] += hh_stats["parsed"]
                    stats["hh"]["saved"] += hh_stats["saved"]
                    stats["hh"]["requests"] += 1
                except Exception as e:
                    stats["errors"].append(f"HH [{i}]: {str(e)}")
                    print(f"[MassParsingJob] HH error: {e}")

                # Проверяем отмену после HH
                if cancel_event.is_set():
                    print(f"[MassParsingJob] Cancel requested after HH parsing at iteration {i+1}, stopping...")
                    stats["status"] = "cancelled"
                    break

                # Умная задержка после HH
                await smart_delay(i, "hh")

                # SuperJob (batch до 30)
                try:
                    sj_batch = get_mass_batch_size("superjob")
                    sj_stats = await self._parse_source("superjob", self.sj_parser, query, city, sj_batch)
                    stats["superjob"]["parsed"] += sj_stats["parsed"]
                    stats["superjob"]["saved"] += sj_stats["saved"]
                    stats["superjob"]["requests"] += 1
                except Exception as e:
                    stats["errors"].append(f"SuperJob [{i}]: {str(e)}")
                    print(f"[MassParsingJob] SuperJob error: {e}")

                # Умная задержка после SuperJob
                await smart_delay(i, "superjob")

                # Микропауза каждые N запросов
                if (i + 1) % self.settings.micro_break_every == 0:
                    await micro_break()
                    # Меняем User-Agent после микропаузы
                    self.user_agent = get_random_user_agent()

            stats["total_saved"] = stats["hh"]["saved"] + stats["superjob"]["saved"]
            stats["unique_cities"] = list(cities_seen)
            stats["unique_queries"] = list(queries_seen)

        except Exception as e:
            stats["errors"].append(f"General: {str(e)}")
            print(f"[MassParsingJob] General error: {e}")

        finally:
            self.is_running = False
            end_time = datetime.now()
            stats["ended_at"] = end_time.isoformat()
            stats["duration_seconds"] = (end_time - start_time).total_seconds()

        print(f"[MassParsingJob] Finished. Total saved: {stats['total_saved']}")
        print(f"[MassParsingJob] HH: {stats['hh']['saved']}, SuperJob: {stats['superjob']['saved']}")
        print(f"[MassParsingJob] Duration: {stats['duration_seconds']:.0f}s")

        return stats

    async def _parse_source(
        self,
        source: str,
        parser,
        query: str,
        city: str,
        batch_size: int,
    ) -> dict:
        """Парсинг одного источника с увеличенным batch"""
        stats = {"parsed": 0, "saved": 0}

        try:
            filters = SearchFilters(query=query, city=city)
            vacancies = await parser.search(filters, limit=batch_size)
            stats["parsed"] = len(vacancies)

            # Фильтруем военную тематику и сохраняем в БД
            for vacancy in vacancies:
                if is_military_vacancy(vacancy):
                    print(f"[ParsingJob] Skipped military vacancy: {vacancy.title}")
                    continue
                if await vacancy_storage_service.save_vacancy(vacancy):
                    stats["saved"] += 1

            print(f"[MassParsingJob] {source}: parsed={stats['parsed']}, saved={stats['saved']}")

        except Exception as e:
            print(f"[MassParsingJob] {source} parse error: {e}")

        return stats


class AvitoParsingJob:
    """
    Отдельный job для парсинга Avito.
    Запускается каждые 4 часа с увеличенными задержками.
    ОТКЛЮЧЕН - Avito слишком агрессивно блокирует.
    """

    def __init__(self):
        self.settings = get_settings()
        self.avito_parser = AvitoParser()
        self.is_running = False

    async def run(self) -> dict:
        """
        Запустить парсинг Avito в щадящем режиме.
        """
        if self.is_running:
            print("[AvitoJob] Already running, skipping...")
            return {"status": "skipped", "reason": "already_running"}

        self.is_running = True
        start_time = datetime.now()
        stats = {
            "started_at": start_time.isoformat(),
            "parsed": 0,
            "saved": 0,
            "errors": [],
        }

        print(f"[AvitoJob] Starting at {start_time}")

        try:
            # Меньше запросов и городов для Avito (щадящий режим)
            queries_to_parse = get_random_queries(5)  # 5 случайных запросов
            cities_to_parse = get_random_cities(4)    # 4 города (Москва + СПб + 2 случайных)
            print(f"[AvitoJob] Queries: {queries_to_parse}")
            print(f"[AvitoJob] Cities: {cities_to_parse}")

            for query in queries_to_parse:
                for city in cities_to_parse:
                    try:
                        print(f"[AvitoJob] Parsing: {query} in {city}")

                        filters = SearchFilters(query=query, city=city)
                        vacancies = await self.avito_parser.search(filters, limit=10)
                        stats["parsed"] += len(vacancies)

                        # Фильтруем военную тематику и сохраняем
                        for vacancy in vacancies:
                            if is_military_vacancy(vacancy):
                                print(f"[AvitoJob] Skipped military vacancy: {vacancy.title}")
                                continue
                            if await vacancy_storage_service.save_vacancy(vacancy):
                                stats["saved"] += 1

                        # Большая задержка после каждого запроса (30-60 сек)
                        delay = 30 + (asyncio.get_event_loop().time() % 30)
                        print(f"[AvitoJob] Waiting {delay:.0f}s before next request...")
                        await asyncio.sleep(delay)

                    except Exception as e:
                        print(f"[AvitoJob] Error for {query}/{city}: {e}")
                        stats["errors"].append(f"{query}/{city}: {str(e)}")
                        # При ошибке ждём 3 минуты
                        await asyncio.sleep(180)

                # Перерыв между запросами (2-3 минуты)
                await asyncio.sleep(120 + (asyncio.get_event_loop().time() % 60))

        except Exception as e:
            stats["errors"].append(f"General: {str(e)}")
            print(f"[AvitoJob] General error: {e}")

        finally:
            self.is_running = False
            end_time = datetime.now()
            stats["ended_at"] = end_time.isoformat()
            stats["duration_seconds"] = (end_time - start_time).total_seconds()

        print(f"[AvitoJob] Finished. Stats: {stats}")
        return stats


class VerificationJob:
    """
    Job для проверки существующих вакансий.
    Запускается каждый час.
    """

    def __init__(self):
        self.is_running = False

    async def run(self) -> dict:
        """
        Проверить существующие вакансии.
        Пометить неактивные.
        """
        if self.is_running:
            print("[VerificationJob] Already running, skipping...")
            return {"status": "skipped", "reason": "already_running"}

        self.is_running = True
        start_time = datetime.now()
        stats = {
            "started_at": start_time.isoformat(),
            "checked": 0,
            "marked_inactive": 0,
            "still_active": 0,
            "errors": 0,
        }

        print(f"[VerificationJob] Starting at {start_time}")

        try:
            # Получаем вакансии для проверки (oldest first)
            vacancies = await vacancy_storage_service.get_for_verification(limit=50)
            print(f"[VerificationJob] Got {len(vacancies)} vacancies to verify")

            for vacancy in vacancies:
                try:
                    is_active = await self._check_vacancy(vacancy)
                    stats["checked"] += 1

                    if is_active:
                        await vacancy_storage_service.update_last_checked(vacancy.id)
                        stats["still_active"] += 1
                    else:
                        await vacancy_storage_service.mark_inactive(vacancy.id)
                        stats["marked_inactive"] += 1
                        print(f"[VerificationJob] Marked inactive: {vacancy.title}")

                    # Задержка между проверками
                    await human_delay(min_seconds=2, max_seconds=5)

                except Exception as e:
                    print(f"[VerificationJob] Error checking {vacancy.id}: {e}")
                    stats["errors"] += 1

        except Exception as e:
            print(f"[VerificationJob] General error: {e}")

        finally:
            self.is_running = False
            end_time = datetime.now()
            stats["ended_at"] = end_time.isoformat()
            stats["duration_seconds"] = (end_time - start_time).total_seconds()

        print(f"[VerificationJob] Finished. Stats: {stats}")
        return stats

    async def _check_vacancy(self, vacancy: StoredVacancy) -> bool:
        """
        Проверить, активна ли вакансия.
        Делаем HEAD или GET запрос к URL.
        """
        try:
            async with httpx.AsyncClient() as client:
                # Сначала пробуем HEAD (быстрее)
                response = await client.head(
                    vacancy.url,
                    timeout=10.0,
                    follow_redirects=True,
                )

                # 200 = активна, 404/410 = неактивна
                if response.status_code == 200:
                    return True
                elif response.status_code in (404, 410, 301):
                    return False

                # Для других кодов пробуем GET
                response = await client.get(
                    vacancy.url,
                    timeout=10.0,
                    follow_redirects=True,
                )

                # Проверяем контент на признаки удалённой вакансии
                if response.status_code == 200:
                    text = response.text.lower()
                    inactive_markers = [
                        "вакансия в архиве",
                        "вакансия удалена",
                        "вакансия закрыта",
                        "vacancy not found",
                        "страница не найдена",
                        "объявление снято",
                        "объявление удалено",
                    ]
                    for marker in inactive_markers:
                        if marker in text:
                            return False
                    return True

                return response.status_code < 400

        except httpx.TimeoutException:
            # Таймаут — считаем активной (не уверены)
            return True
        except Exception as e:
            print(f"[VerificationJob] Check error for {vacancy.url}: {e}")
            # При ошибке считаем активной
            return True


class ModerationJob:
    """
    Job для модерации контента вакансий с помощью AI.
    Проверяет вакансии на запрещённый контент (военная тематика и т.д.).
    Запускается каждый час.
    """

    def __init__(self):
        self.is_running = False

    async def run(self) -> dict:
        """
        Запустить модерацию вакансий.
        Возвращает статистику.
        """
        if self.is_running:
            print("[ModerationJob] Already running, skipping...")
            return {"status": "skipped", "reason": "already_running"}

        self.is_running = True
        start_time = datetime.now()
        stats = {
            "started_at": start_time.isoformat(),
            "checked": 0,
            "rejected": 0,
            "approved": 0,
            "errors": [],
        }

        print(f"[ModerationJob] Starting content moderation at {start_time}")

        try:
            from agents.content_moderator import ContentModerator

            moderator = ContentModerator()

            # Умная выборка вакансий для проверки:
            # 1. Новые (за последние 24 часа) - ПРИОРИТЕТ
            # 2. Старые непроверенные (moderation_checked_at IS NULL)
            vacancies = await vacancy_storage_service.get_vacancies_for_moderation(limit=500)

            if not vacancies:
                print("[ModerationJob] No vacancies to moderate")
                self.is_running = False
                return {
                    **stats,
                    "status": "completed",
                    "ended_at": datetime.now().isoformat(),
                }

            print(f"[ModerationJob] Found {len(vacancies)} vacancies to check")
            stats["checked"] = len(vacancies)

            # Проверяем батчами по 50 вакансий (чтобы не перегружать AI)
            batch_size = 50
            total_rejected = 0
            total_approved = 0

            for i in range(0, len(vacancies), batch_size):
                batch = vacancies[i : i + batch_size]
                batch_num = i // batch_size + 1
                total_batches = (len(vacancies) + batch_size - 1) // batch_size
                print(f"[ModerationJob] Checking batch {batch_num}/{total_batches}")

                # Проверяем батч
                result = await moderator.check_vacancies(batch)

                approved = result.get("approved", [])
                rejected = result.get("rejected", [])

                # Помечаем отклонённые вакансии как неактивные
                for vacancy in rejected:
                    try:
                        await vacancy_storage_service.deactivate_vacancy(vacancy.id)
                        total_rejected += 1
                    except Exception as e:
                        print(f"[ModerationJob] Failed to deactivate {vacancy.id}: {e}")
                        stats["errors"].append(f"Failed to deactivate {vacancy.id}")

                # Помечаем одобренные вакансии как проверенные
                for vacancy in approved:
                    try:
                        await vacancy_storage_service.mark_moderation_checked(vacancy.id)
                        total_approved += 1
                    except Exception as e:
                        print(f"[ModerationJob] Failed to mark {vacancy.id}: {e}")
                        stats["errors"].append(f"Failed to mark {vacancy.id}")

                # Небольшая задержка между батчами
                if i + batch_size < len(vacancies):
                    await asyncio.sleep(2)

            stats["rejected"] = total_rejected
            stats["approved"] = total_approved
            stats["ended_at"] = datetime.now().isoformat()
            stats["duration_seconds"] = (datetime.now() - start_time).total_seconds()

            # Получаем статистику остатка
            moderation_stats = await vacancy_storage_service.get_moderation_stats()
            stats["remaining_unchecked"] = moderation_stats.get("unchecked", 0)

            print(
                f"[ModerationJob] Completed: {stats['approved']} approved, {stats['rejected']} rejected"
            )
            print(
                f"[ModerationJob] Remaining unchecked: {stats['remaining_unchecked']} vacancies"
            )

        except Exception as e:
            print(f"[ModerationJob] Error: {e}")
            stats["errors"].append(str(e))
            stats["status"] = "error"
        finally:
            self.is_running = False

        return stats


# Singleton instances
parsing_job = ParsingJob()
avito_job = AvitoParsingJob()
verification_job = VerificationJob()
mass_parsing_job = MassParsingJob()  # Новый массовый парсинг
moderation_job = ModerationJob()  # Модерация контента


async def run_parsing():
    """Wrapper для запуска парсинга HH/SuperJob с сохранением истории"""
    from services.scheduler_service import scheduler_service

    # Проверяем, не на паузе ли джоб
    state = await scheduler_service.get_job_state("parsing_job")
    if state.get("is_paused"):
        print("[ParsingJob] Job is paused, skipping...")
        return {"status": "skipped", "reason": "paused"}

    start_time = datetime.now()
    stats = await parsing_job.run()
    end_time = datetime.now()

    # Сохраняем в историю
    status = "completed"
    if stats.get("errors"):
        status = "completed_with_errors"
    if stats.get("status") == "skipped":
        status = "skipped"

    await scheduler_service.save_job_history(
        job_id="parsing_job",
        job_name="HH/SuperJob Parsing",
        status=status,
        started_at=start_time,
        ended_at=end_time,
        stats=stats,
    )

    return stats


async def run_avito_parsing():
    """Wrapper для запуска парсинга Avito с сохранением истории"""
    from services.scheduler_service import scheduler_service

    # Проверяем, не на паузе ли джоб
    state = await scheduler_service.get_job_state("avito_job")
    if state.get("is_paused"):
        print("[AvitoJob] Job is paused, skipping...")
        return {"status": "skipped", "reason": "paused"}

    start_time = datetime.now()
    stats = await avito_job.run()
    end_time = datetime.now()

    # Сохраняем в историю
    status = "completed"
    if stats.get("errors"):
        status = "completed_with_errors"
    if stats.get("status") == "skipped":
        status = "skipped"

    await scheduler_service.save_job_history(
        job_id="avito_job",
        job_name="Avito Parsing",
        status=status,
        started_at=start_time,
        ended_at=end_time,
        stats=stats,
    )

    return stats


async def run_verification():
    """Wrapper для запуска верификации с сохранением истории"""
    from services.scheduler_service import scheduler_service

    # Проверяем, не на паузе ли джоб
    state = await scheduler_service.get_job_state("verification_job")
    if state.get("is_paused"):
        print("[VerificationJob] Job is paused, skipping...")
        return {"status": "skipped", "reason": "paused"}

    start_time = datetime.now()
    stats = await verification_job.run()
    end_time = datetime.now()

    # Сохраняем в историю
    status = "completed"
    if stats.get("errors") and stats["errors"] > 0:
        status = "completed_with_errors"
    if stats.get("status") == "skipped":
        status = "skipped"

    await scheduler_service.save_job_history(
        job_id="verification_job",
        job_name="Vacancy Verification",
        status=status,
        started_at=start_time,
        ended_at=end_time,
        stats=stats,
    )

    return stats


async def run_mass_parsing():
    """Wrapper для запуска массового парсинга HH/SuperJob с сохранением истории"""
    from services.scheduler_service import scheduler_service

    # Проверяем, не на паузе ли джоб
    state = await scheduler_service.get_job_state("mass_parsing_job")
    if state.get("is_paused"):
        print("[MassParsingJob] Job is paused, skipping...")
        return {"status": "skipped", "reason": "paused"}

    # Проверяем рабочие часы (8:00-23:00)
    if not is_working_hours():
        print("[MassParsingJob] Outside working hours, skipping...")
        return {"status": "skipped", "reason": "outside_working_hours"}

    start_time = datetime.now()
    stats = await mass_parsing_job.run()
    end_time = datetime.now()

    # Сохраняем в историю
    status = "completed"
    if stats.get("errors"):
        status = "completed_with_errors"
    if stats.get("status") == "skipped":
        status = "skipped"

    await scheduler_service.save_job_history(
        job_id="mass_parsing_job",
        job_name="Mass HH/SuperJob Parsing",
        status=status,
        started_at=start_time,
        ended_at=end_time,
        stats=stats,
    )

    return stats


async def run_moderation():
    """Wrapper для запуска модерации контента с сохранением истории"""
    from services.scheduler_service import scheduler_service

    # Проверяем, не на паузе ли джоб
    state = await scheduler_service.get_job_state("moderation_job")
    if state.get("is_paused"):
        print("[ModerationJob] Job is paused, skipping...")
        return {"status": "skipped", "reason": "paused"}

    start_time = datetime.now()
    stats = await moderation_job.run()
    end_time = datetime.now()

    # Сохраняем в историю
    status = "completed"
    if stats.get("errors"):
        status = "completed_with_errors"
    if stats.get("status") == "skipped":
        status = "skipped"

    await scheduler_service.save_job_history(
        job_id="moderation_job",
        job_name="Content Moderation",
        status=status,
        started_at=start_time,
        ended_at=end_time,
        stats=stats,
    )

    return stats
