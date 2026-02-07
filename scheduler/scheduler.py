"""
APScheduler настройка и управление
- Mass HH/SuperJob: каждые 2 часа (50 запросов, ~10k вакансий)
- Avito: ОТКЛЮЧЕН (слишком агрессивные блокировки)
- Верификация: каждый час
"""

import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from typing import Dict, Optional

from scheduler.jobs import run_parsing, run_avito_parsing, run_verification, run_mass_parsing, run_moderation
from scheduler.human_behavior import is_working_hours
from services.scheduler_service import scheduler_service


# Глобальный scheduler
scheduler: AsyncIOScheduler = None

# Отслеживание выполняющихся jobs
# {job_id: {"started_at": datetime, "task": asyncio.Task}}
running_jobs: Dict[str, dict] = {}


def get_scheduler() -> AsyncIOScheduler:
    """Получить или создать scheduler"""
    global scheduler
    if scheduler is None:
        scheduler = AsyncIOScheduler(
            timezone="Europe/Moscow",
            job_defaults={
                "coalesce": True,  # Объединять пропущенные запуски
                "max_instances": 1,  # Только один инстанс job одновременно
                "misfire_grace_time": 300,  # 5 минут на опоздание
            }
        )
    return scheduler


def mark_job_started(job_id: str):
    """Отметить, что джоб начал выполняться"""
    running_jobs[job_id] = {
        "started_at": datetime.now(),
    }

def mark_job_finished(job_id: str):
    """Отметить, что джоб закончил выполняться"""
    running_jobs.pop(job_id, None)

def is_job_running(job_id: str) -> bool:
    """Проверить, выполняется ли джоб прямо сейчас"""
    return job_id in running_jobs

def get_job_start_time(job_id: str) -> Optional[datetime]:
    """Получить время начала выполнения джоба"""
    info = running_jobs.get(job_id)
    return info["started_at"] if info else None

def get_all_running_jobs() -> Dict[str, dict]:
    """Получить все выполняющиеся джобы"""
    return running_jobs.copy()


async def _run_with_tracking(job_id: str, coro):
    """Запустить корутину с отслеживанием статуса"""
    mark_job_started(job_id)
    try:
        result = await coro
        return result
    finally:
        mark_job_finished(job_id)


async def parsing_job_wrapper():
    """
    Wrapper для старого парсинга (оставлен для совместимости).
    Используйте mass_parsing_job_wrapper для нового массового парсинга.
    """
    if not is_working_hours():
        print(f"[Scheduler] Skipping parsing - outside working hours")
        return

    print(f"[Scheduler] Starting parsing job at {datetime.now()}")
    await _run_with_tracking("parsing_job", run_parsing())
    print(f"[Scheduler] Parsing job finished")


async def mass_parsing_job_wrapper():
    """
    Wrapper для массового парсинга HH/SuperJob.
    50 запросов за сеанс, цель ~10k вакансий.
    Парсим только в рабочие часы (8:00-23:00).
    """
    if not is_working_hours():
        print(f"[Scheduler] Skipping mass parsing - outside working hours")
        return

    print(f"[Scheduler] Starting MASS parsing job at {datetime.now()}")
    try:
        stats = await _run_with_tracking("mass_parsing_job", run_mass_parsing())
        total_saved = stats.get('total_saved', 0)
        hh_saved = stats.get('hh', {}).get('saved', 0)
        sj_saved = stats.get('superjob', {}).get('saved', 0)
        print(f"[Scheduler] Mass parsing completed: {total_saved} vacancies saved (HH: {hh_saved}, SJ: {sj_saved})")
    except Exception as e:
        print(f"[Scheduler] Mass parsing error: {e}")


async def avito_job_wrapper():
    """
    Wrapper для парсинга Avito (отдельно от остальных).
    Запускается реже — каждые 4 часа.
    """
    if not is_working_hours():
        print(f"[Scheduler] Skipping Avito parsing - outside working hours")
        return

    print(f"[Scheduler] Starting Avito parsing job at {datetime.now()}")
    try:
        stats = await _run_with_tracking("avito_job", run_avito_parsing())
        print(f"[Scheduler] Avito completed: {stats.get('saved', 0)} vacancies saved")
    except Exception as e:
        print(f"[Scheduler] Avito error: {e}")


async def verification_job_wrapper():
    """Wrapper для верификации"""
    print(f"[Scheduler] Starting verification job at {datetime.now()}")
    try:
        stats = await _run_with_tracking("verification_job", run_verification())
        print(f"[Scheduler] Verification completed: {stats.get('checked', 0)} checked, {stats.get('marked_inactive', 0)} inactive")
    except Exception as e:
        print(f"[Scheduler] Verification error: {e}")


async def moderation_job_wrapper():
    """Wrapper для AI модерации контента"""
    print(f"[Scheduler] Starting content moderation job at {datetime.now()}")
    try:
        stats = await _run_with_tracking("moderation_job", run_moderation())
        print(f"[Scheduler] Moderation completed: {stats.get('checked', 0)} checked, {stats.get('approved', 0)} approved, {stats.get('rejected', 0)} rejected")
    except Exception as e:
        print(f"[Scheduler] Moderation error: {e}")


async def volume_stats_wrapper():
    """Wrapper для записи статистики объёма вакансий (для графика)"""
    print(f"[Scheduler] Recording volume stats at {datetime.now()}")
    try:
        await _run_with_tracking("volume_stats_job", scheduler_service.record_volume_stats())
        print(f"[Scheduler] Volume stats recorded")
    except Exception as e:
        print(f"[Scheduler] Volume stats error: {e}")



def start_scheduler():
    """Запустить scheduler с jobs"""
    sched = get_scheduler()

    # МАССОВЫЙ парсинг HH/SuperJob каждые 2 часа (50 запросов, ~10k вакансий)
    sched.add_job(
        mass_parsing_job_wrapper,
        trigger=IntervalTrigger(hours=2),
        id="mass_parsing_job",
        name="Mass HH/SuperJob Parsing",
        replace_existing=True,
    )

    # AVITO ОТКЛЮЧЕН - слишком агрессивные блокировки
    # sched.add_job(
    #     avito_job_wrapper,
    #     trigger=IntervalTrigger(hours=4),
    #     id="avito_job",
    #     name="Avito Parsing",
    #     replace_existing=True,
    # )

    # Верификация каждый час
    sched.add_job(
        verification_job_wrapper,
        trigger=IntervalTrigger(hours=1),
        id="verification_job",
        name="Vacancy Verification",
        replace_existing=True,
    )

    # AI модерация контента каждый час
    sched.add_job(
        moderation_job_wrapper,
        trigger=IntervalTrigger(hours=1),
        id="moderation_job",
        name="Content Moderation",
        replace_existing=True,
    )

    # Запись статистики объёма каждый час (для графика в админке)
    sched.add_job(
        volume_stats_wrapper,
        trigger=IntervalTrigger(hours=1),
        id="volume_stats_job",
        name="Volume Stats Recording",
        replace_existing=True,
    )

    # Запускаем scheduler
    if not sched.running:
        sched.start()
        print(f"[Scheduler] Started with jobs:")
        for job in sched.get_jobs():
            print(f"  - {job.name}: {job.trigger}")

    return sched


def shutdown_scheduler():
    """Остановить scheduler"""
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        print("[Scheduler] Shutdown complete")
        scheduler = None


def get_job_status() -> dict:
    """Получить статус jobs"""
    sched = get_scheduler()
    jobs_info = []

    for job in sched.get_jobs():
        is_running = is_job_running(job.id)
        started_at = get_job_start_time(job.id)

        jobs_info.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
            "is_running": is_running,
            "started_at": started_at.isoformat() if started_at else None,
        })

    return {
        "running": sched.running,
        "jobs": jobs_info,
    }


async def trigger_parsing_now():
    """Запустить МАССОВЫЙ парсинг вручную (для тестирования)"""
    print("[Scheduler] Manual MASS parsing triggered")
    return await _run_with_tracking("mass_parsing_job", run_mass_parsing())


async def trigger_old_parsing_now():
    """Запустить старый парсинг вручную (для совместимости)"""
    print("[Scheduler] Manual old parsing triggered")
    return await _run_with_tracking("parsing_job", run_parsing())


async def trigger_verification_now():
    """Запустить верификацию вручную (для тестирования)"""
    print("[Scheduler] Manual verification triggered")
    return await _run_with_tracking("verification_job", run_verification())


async def trigger_moderation_now():
    """Запустить AI модерацию вручную (для тестирования)"""
    print("[Scheduler] Manual moderation triggered")
    return await _run_with_tracking("moderation_job", run_moderation())
