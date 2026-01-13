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
)
from scheduler.jobs import run_avito_parsing


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
    "parsing_job": "HH/SuperJob",
    "avito_job": "Avito",
    "verification_job": "Верификация",
}

VALID_JOB_IDS = ["parsing_job", "avito_job", "verification_job"]


# === Routes ===

@router.get("/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status(authorization: str = Header(None)):
    """Получить полный статус планировщика со всеми джобами"""
    await require_admin(authorization)

    scheduler_info = get_job_status()
    job_states = await scheduler_service.get_all_job_states()

    jobs = []
    for job_info in scheduler_info.get("jobs", []):
        job_id = job_info["id"]
        state = job_states.get(job_id, {"is_paused": False})
        last_run = await scheduler_service.get_last_run_stats(job_id)

        # Определяем статус
        is_paused = state.get("is_paused", False)
        if is_paused:
            status = "paused"
        else:
            status = "active"

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

    success = await scheduler_service.set_job_paused(job_id, False, user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to resume job")

    return {"success": True, "job_id": job_id, "is_paused": False}


@router.post("/jobs/{job_id}/trigger")
async def trigger_job(job_id: str, authorization: str = Header(None)):
    """Запустить джоб вручную"""
    await require_admin(authorization)

    if job_id not in VALID_JOB_IDS:
        raise HTTPException(status_code=400, detail="Invalid job ID")

    try:
        if job_id == "parsing_job":
            stats = await trigger_parsing_now()
        elif job_id == "avito_job":
            stats = await run_avito_parsing()
        elif job_id == "verification_job":
            stats = await trigger_verification_now()
        else:
            raise HTTPException(status_code=400, detail="Invalid job ID")

        return {"success": True, "job_id": job_id, "stats": stats}
    except Exception as e:
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
