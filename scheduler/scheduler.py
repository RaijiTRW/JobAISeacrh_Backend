"""
APScheduler настройка и управление
Парсинг каждые 2 часа, верификация каждый час
"""

import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from scheduler.jobs import run_parsing, run_verification
from scheduler.human_behavior import is_working_hours


# Глобальный scheduler
scheduler: AsyncIOScheduler = None


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


async def parsing_job_wrapper():
    """
    Wrapper для парсинга с проверкой рабочих часов.
    Парсим только днём для естественности.
    """
    if not is_working_hours():
        print(f"[Scheduler] Skipping parsing - outside working hours")
        return

    print(f"[Scheduler] Starting parsing job at {datetime.now()}")
    try:
        stats = await run_parsing()
        print(f"[Scheduler] Parsing completed: {stats.get('total_saved', 0)} vacancies saved")
    except Exception as e:
        print(f"[Scheduler] Parsing error: {e}")


async def verification_job_wrapper():
    """Wrapper для верификации"""
    print(f"[Scheduler] Starting verification job at {datetime.now()}")
    try:
        stats = await run_verification()
        print(f"[Scheduler] Verification completed: {stats.get('checked', 0)} checked, {stats.get('marked_inactive', 0)} inactive")
    except Exception as e:
        print(f"[Scheduler] Verification error: {e}")


def start_scheduler():
    """Запустить scheduler с jobs"""
    sched = get_scheduler()

    # Парсинг каждые 2 часа (в рабочее время)
    sched.add_job(
        parsing_job_wrapper,
        trigger=IntervalTrigger(hours=2),
        id="parsing_job",
        name="Vacancy Parsing",
        replace_existing=True,
    )

    # Верификация каждый час
    sched.add_job(
        verification_job_wrapper,
        trigger=IntervalTrigger(hours=1),
        id="verification_job",
        name="Vacancy Verification",
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
        jobs_info.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        })

    return {
        "running": sched.running,
        "jobs": jobs_info,
    }


async def trigger_parsing_now():
    """Запустить парсинг вручную (для тестирования)"""
    print("[Scheduler] Manual parsing triggered")
    return await run_parsing()


async def trigger_verification_now():
    """Запустить верификацию вручную (для тестирования)"""
    print("[Scheduler] Manual verification triggered")
    return await run_verification()
