"""
Scheduler API Routes - Управление парсингом и мониторинг
"""
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from services.scheduler_service import scheduler_service
from services.admin_service import admin_service
from services.auth_service import get_user_from_token
from scheduler.scheduler import (
    get_scheduler,
    get_job_status,
    trigger_parsing_now,
    trigger_verification_now,
    trigger_old_parsing_now,
)
from scheduler.jobs import run_avito_parsing, run_mass_parsing, run_moderation


router = APIRouter(prefix="/api/admin/scheduler", tags=["scheduler"])


# === Models ===

class JobStatus(BaseModel):
    job_id: str
    name: str
    status: str  # active, paused, running
    is_paused: bool
    next_run: Optional[str] = None
    last_run: Optional[dict] = None
    trigger: str


class SchedulerStatusResponse(BaseModel):
    scheduler_running: bool
    jobs: list[JobStatus]


class JobHistoryItem(BaseModel):
    id: str
    job_id: str
    job_name: str
    status: str
    started_at: str
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    stats: dict


class VolumeDataPoint(BaseModel):
    recorded_at: str
    hh: int
    superjob: int
    avito: int
    platform: int
    total: int


# === Helpers ===

async def require_admin(authorization: str) -> str:
    """Проверить, что пользователь — админ"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")

    token = authorization.replace("Bearer ", "")
    user = get_user_from_token(token)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user")

    is_admin = await admin_service.is_admin(user_id)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    return user_id


JOB_NAMES = {
    "mass_parsing_job": "Парсинг ленты",
    "parsing_job": "HH/SuperJob (старый)",
    "avito_job": "Avito (отключён)",
    "verification_job": "Верификация",
    "moderation_job": "Content Moderation",
    "volume_stats_job": "Volume Stats Recording",
}

VALID_JOB_IDS = ["mass_parsing_job", "parsing_job", "avito_job", "verification_job", "moderation_job", "volume_stats_job"]


# === Routes ===

@router.get("/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status(authorization: str = Header(None)):
    """Получить полный статус планировщика со всеми джобами"""
    await require_admin(authorization)

    print(f"\n[API] ===== /status START =====")
    scheduler_info = get_job_status()
    print(f"[API] scheduler_info: {scheduler_info}")

    job_states = await scheduler_service.get_all_job_states()
    print(f"[API] job_states from DB: {job_states}")

    jobs = []
    for job_info in scheduler_info.get("jobs", []):
        job_id = job_info["id"]
        state = job_states.get(job_id, {"is_paused": False})
        last_run = await scheduler_service.get_last_run_stats(job_id)

        # Определяем статус
        is_running = job_info.get("is_running", False)
        is_paused = state.get("is_paused", False)

        if is_running:
            status = "running"
        elif is_paused:
            status = "paused"
        else:
            status = "active"

        print(f"[API] {job_id}: is_paused={is_paused}, status={status}, next_run={job_info.get('next_run')}")

        jobs.append(JobStatus(
            job_id=job_id,
            name=JOB_NAMES.get(job_id, job_info["name"]),
            status=status,
            is_paused=is_paused,
            next_run=job_info.get("next_run") if not is_paused else None,
            last_run={
                "started_at": last_run.get("started_at"),
                "ended_at": last_run.get("ended_at"),
                "duration_seconds": last_run.get("duration_seconds"),
                "stats": last_run.get("stats", {}),
            } if last_run else None,
            trigger=job_info.get("trigger", ""),
        ))

    print(f"[API] Returning {len(jobs)} jobs")
    for job in jobs:
        print(f"[API]   {job.job_id}: status={job.status}, is_paused={job.is_paused}, next_run={job.next_run}")
    print(f"[API] ===== /status END =====\n")

    return SchedulerStatusResponse(
        scheduler_running=scheduler_info.get("running", False),
        jobs=jobs,
    )


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str, authorization: str = Header(None)):
    """Поставить джоб на паузу"""
    user_id = await require_admin(authorization)

    if job_id not in VALID_JOB_IDS:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    job_name = JOB_NAMES.get(job_id, job_id)
    print(f"\n{'='*60}")
    print(f"[ADMIN PAUSE] {job_name} ({job_id})")
    print(f"  Пользователь: {user_id}")
    print(f"  Действие: ОТКЛЮЧИТЬ автоматический запуск")
    print(f"{'='*60}\n")

    success = await scheduler_service.set_job_paused(job_id, True, user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to pause job")

    return {"success": True, "job_id": job_id, "is_paused": True}


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, authorization: str = Header(None)):
    """Возобновить джоб"""
    user_id = await require_admin(authorization)

    if job_id not in VALID_JOB_IDS:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    job_name = JOB_NAMES.get(job_id, job_id)
    print(f"\n{'='*60}")
    print(f"[ADMIN RESUME] {job_name} ({job_id})")
    print(f"  Пользователь: {user_id}")
    print(f"  Действие: ВКЛЮЧИТЬ автоматический запуск")
    print(f"{'='*60}\n")

    success = await scheduler_service.set_job_paused(job_id, False, user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to resume job")

    # Очищаем событие отмены на случай если оно было установлено
    from scheduler.scheduler import clear_job_cancel_event
    clear_job_cancel_event(job_id)

    return {"success": True, "job_id": job_id, "is_paused": False}


@router.post("/jobs/{job_id}/stop")
async def stop_job(job_id: str, authorization: str = Header(None)):
    """Остановить выполняющийся джоб"""
    user_id = await require_admin(authorization)

    if job_id not in VALID_JOB_IDS:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    job_name = JOB_NAMES.get(job_id, job_id)
    print(f"\n{'='*60}")
    print(f"[ADMIN STOP] {job_name} ({job_id})")
    print(f"  Пользователь: {user_id}")
    print(f"  Действие: ОСТАНОВИТЬ текущее выполнение + отключить автозапуск")
    print(f"{'='*60}\n")

    # Ставим на паузу чтобы предотвратить следующие запуски
    success = await scheduler_service.set_job_paused(job_id, True, user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to stop job")

    # Запрашиваем отмену текущего выполнения
    from scheduler.scheduler import request_job_cancel
    request_job_cancel(job_id)

    return {"success": True, "job_id": job_id, "is_paused": True}


@router.post("/jobs/{job_id}/trigger")
async def trigger_job(job_id: str, authorization: str = Header(None)):
    """Запустить джоб вручную"""
    user_id = await require_admin(authorization)

    if job_id not in VALID_JOB_IDS:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    job_name = JOB_NAMES.get(job_id, job_id)
    print(f"\n{'='*60}")
    print(f"[ADMIN TRIGGER] {job_name} ({job_id})")
    print(f"  Пользователь: {user_id}")
    print(f"  Действие: РУЧНОЙ ЗАПУСК (вне расписания)")
    print(f"{'='*60}\n")

    try:
        if job_id == "mass_parsing_job":
            stats = await trigger_parsing_now()
        elif job_id == "parsing_job":
            stats = await trigger_old_parsing_now()
        elif job_id == "avito_job":
            stats = await run_avito_parsing()
        elif job_id == "verification_job":
            stats = await trigger_verification_now()
        elif job_id == "moderation_job":
            stats = await run_moderation()
        elif job_id == "volume_stats_job":
            await scheduler_service.record_volume_stats()
            stats = {"status": "completed"}
        else:
            raise HTTPException(status_code=400, detail="Invalid job ID")

        return {"success": True, "job_id": job_id, "stats": stats}
    except Exception as e:
        print(f"[ADMIN TRIGGER] ОШИБКА при выполнении {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Job execution failed: {str(e)}")


@router.get("/history")
async def get_job_history(
    job_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    authorization: str = Header(None),
) -> list[JobHistoryItem]:
    """Получить историю выполнения джобов"""
    await require_admin(authorization)

    if job_id and job_id not in VALID_JOB_IDS:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    history = await scheduler_service.get_job_history(job_id, limit)

    return [
        JobHistoryItem(
            id=item["id"],
            job_id=item["job_id"],
            job_name=item["job_name"],
            status=item["status"],
            started_at=item["started_at"],
            ended_at=item.get("ended_at"),
            duration_seconds=item.get("duration_seconds"),
            stats=item.get("stats", {}),
        )
        for item in history
    ]


@router.get("/volume")
async def get_volume_stats(
    hours: int = Query(168, ge=1, le=720),  # Default 7 days, max 30 days
    authorization: str = Header(None),
) -> list[VolumeDataPoint]:
    """Получить историю объёма вакансий для графика"""
    await require_admin(authorization)

    volume_data = await scheduler_service.get_volume_history(hours)

    return [
        VolumeDataPoint(
            recorded_at=item["recorded_at"],
            hh=item.get("hh", 0),
            superjob=item.get("superjob", 0),
            avito=item.get("avito", 0),
            platform=item.get("platform", 0),
            total=item.get("total", 0),
        )
        for item in volume_data
    ]
