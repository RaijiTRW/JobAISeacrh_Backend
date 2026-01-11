"""
Scheduled jobs для парсинга и проверки вакансий
"""

import asyncio
import httpx
from datetime import datetime
from typing import Optional

from config import get_settings
from models.vacancy import SearchFilters
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
    POPULAR_QUERIES,
    POPULAR_CITIES,
)


class ParsingJob:
    """
    Job для парсинга вакансий.
    Запускается каждые 2 часа.
    """

    def __init__(self):
        self.settings = get_settings()
        self.hh_parser = HHParser()
        self.avito_parser = AvitoParser()
        self.sj_parser = SuperJobParser()
        self.is_running = False

    async def run(self) -> dict:
        """
        Запустить полный цикл парсинга.
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
            "avito": {"parsed": 0, "saved": 0},
            "superjob": {"parsed": 0, "saved": 0},
            "total_saved": 0,
            "errors": [],
        }

        print(f"[ParsingJob] Starting at {start_time}")

        try:
            # Парсим по популярным запросам и городам
            queries_to_parse = POPULAR_QUERIES[:10]  # Топ-10 запросов
            cities_to_parse = POPULAR_CITIES[:5]  # Топ-5 городов

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

            # Avito парсим отдельно и очень аккуратно
            print("[ParsingJob] Starting Avito parsing (careful mode)...")
            try:
                avito_stats = await self._parse_avito_careful(
                    queries_to_parse[:5],  # Только 5 запросов для Avito
                    cities_to_parse[:3],   # Только 3 города
                )
                stats["avito"] = avito_stats
            except Exception as e:
                stats["errors"].append(f"Avito: {str(e)}")
                print(f"[ParsingJob] Avito error: {e}")

            stats["total_saved"] = (
                stats["hh"]["saved"] +
                stats["avito"]["saved"] +
                stats["superjob"]["saved"]
            )

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

            # Сохраняем в БД
            for vacancy in vacancies:
                if await vacancy_storage_service.save_vacancy(vacancy):
                    stats["saved"] += 1

            print(f"[ParsingJob] {source}: parsed={stats['parsed']}, saved={stats['saved']}")

        except Exception as e:
            print(f"[ParsingJob] {source} parse error: {e}")

        return stats

    async def _parse_avito_careful(
        self,
        queries: list[str],
        cities: list[str],
    ) -> dict:
        """
        Очень осторожный парсинг Avito.
        Большие задержки, маленькие батчи.
        """
        stats = {"parsed": 0, "saved": 0}
        session = HumanSession("avito")

        for query in queries:
            for city in cities:
                try:
                    await session.before_request()

                    filters = SearchFilters(query=query, city=city)
                    vacancies = await self.avito_parser.search(filters, limit=5)
                    stats["parsed"] += len(vacancies)

                    for vacancy in vacancies:
                        if await vacancy_storage_service.save_vacancy(vacancy):
                            stats["saved"] += 1

                    # Большая задержка после каждого запроса Avito
                    await human_delay(source="avito")

                except Exception as e:
                    print(f"[ParsingJob] Avito error for {query}/{city}: {e}")
                    # При ошибке делаем очень большую паузу
                    await asyncio.sleep(120)

            # Перерыв между запросами
            await asyncio.sleep(60)

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


# Singleton instances
parsing_job = ParsingJob()
verification_job = VerificationJob()


async def run_parsing():
    """Wrapper для запуска парсинга"""
    return await parsing_job.run()


async def run_verification():
    """Wrapper для запуска верификации"""
    return await verification_job.run()
