"""
Scheduler Service - управление состоянием джобов и историей выполнения
"""
import httpx
from datetime import datetime, timedelta
from typing import Optional

from config import get_settings


class SchedulerService:
    """Сервис для работы с историей и состоянием планировщика"""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.supabase_url
        # RLS on scheduler tables requires service_role
        self.api_key = self.settings.supabase_service_key or self.settings.supabase_key

    def _headers(self) -> dict:
        return {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # === Job History ===

    async def save_job_history(
        self,
        job_id: str,
        job_name: str,
        status: str,
        started_at: datetime,
        ended_at: Optional[datetime] = None,
        stats: Optional[dict] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Сохранить результат выполнения джоба в историю"""
        try:
            duration_seconds = None
            if ended_at and started_at:
                duration_seconds = (ended_at - started_at).total_seconds()

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/rest/v1/scheduler_job_history",
                    headers=self._headers(),
                    json={
                        "job_id": job_id,
                        "job_name": job_name,
                        "status": status,
                        "started_at": started_at.isoformat(),
                        "ended_at": ended_at.isoformat() if ended_at else None,
                        "duration_seconds": duration_seconds,
                        "stats": stats or {},
                        "error_message": error_message,
                    },
                    timeout=10.0,
                )
                return response.status_code in (200, 201)
        except Exception as e:
            print(f"[SchedulerService] save_job_history error: {e}")
            return False

    async def get_job_history(
        self,
        job_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Получить историю выполнения джобов"""
        try:
            params = {
                "select": "*",
                "order": "started_at.desc",
                "limit": str(limit),
            }

            if job_id:
                params["job_id"] = f"eq.{job_id}"

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/scheduler_job_history",
                    params=params,
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    return response.json()
            return []
        except Exception as e:
            print(f"[SchedulerService] get_job_history error: {e}")
            return []

    async def get_last_run_stats(self, job_id: str) -> Optional[dict]:
        """Получить статистику последнего выполнения джоба"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/scheduler_job_history",
                    params={
                        "select": "*",
                        "job_id": f"eq.{job_id}",
                        "order": "started_at.desc",
                        "limit": "1",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data:
                        return data[0]
            return None
        except Exception as e:
            print(f"[SchedulerService] get_last_run_stats error: {e}")
            return None

    # === Job State ===

    async def get_job_state(self, job_id: str) -> dict:
        """Получить состояние джоба (пауза/активен)"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/scheduler_job_state",
                    params={
                        "select": "*",
                        "job_id": f"eq.{job_id}",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data:
                        return data[0]
            return {"job_id": job_id, "is_paused": False}
        except Exception as e:
            print(f"[SchedulerService] get_job_state error: {e}")
            return {"job_id": job_id, "is_paused": False}

    async def get_all_job_states(self) -> dict[str, dict]:
        """Получить состояния всех джобов"""
        print(f"[SchedulerService] ===== get_all_job_states START =====")
        try:
            import time
            cache_buster = int(time.time() * 1000)
            url = f"{self.base_url}/rest/v1/scheduler_job_state?_={cache_buster}&select=*"
            print(f"[SchedulerService] URL: {url}")

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers=self._headers(),
                    timeout=10.0,
                )

                print(f"[SchedulerService] Response status: {response.status_code}")

                if response.status_code == 200:
                    data = response.json()
                    print(f"[SchedulerService] Response data: {data}")
                    states = {row["job_id"]: row for row in data}
                    # Логируем состояние паузы для всех джобов
                    for job_id, state in states.items():
                        is_paused = state.get("is_paused", False)
                        print(f"[SchedulerService] {job_id}: is_paused={is_paused}, full_state={state}")
                    print(f"[SchedulerService] Returning {len(states)} states")
                    return states
                else:
                    print(f"[SchedulerService] ERROR Response: {response.text}")
            return {}
        except Exception as e:
            print(f"[SchedulerService] get_all_job_states EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            return {}

    async def set_job_paused(
        self,
        job_id: str,
        is_paused: bool,
        user_id: Optional[str] = None,
    ) -> bool:
        """Установить состояние паузы для джоба"""
        try:
            async with httpx.AsyncClient() as client:
                # Сначала проверяем существует ли запись
                check_response = await client.get(
                    f"{self.base_url}/rest/v1/scheduler_job_state",
                    params={"select": "job_id", "job_id": f"eq.{job_id}"},
                    headers=self._headers(),
                    timeout=10.0,
                )

                record_exists = check_response.status_code == 200 and len(check_response.json()) > 0
                print(f"[SchedulerService] Record exists for {job_id}: {record_exists}")

                update_data = {
                    "job_id": job_id,  # Всегда включаем job_id для вставки
                    "is_paused": is_paused,
                    "updated_at": datetime.utcnow().isoformat(),
                }

                if is_paused:
                    update_data["paused_at"] = datetime.utcnow().isoformat()
                    if user_id:
                        update_data["paused_by"] = user_id
                else:
                    update_data["paused_at"] = None
                    update_data["paused_by"] = None

                if record_exists:
                    # Обновляем существующую запись
                    response = await client.patch(
                        f"{self.base_url}/rest/v1/scheduler_job_state",
                        params={"job_id": f"eq.{job_id}"},
                        headers=self._headers(),
                        json=update_data,
                        timeout=10.0,
                    )
                    print(f"[SchedulerService] PATCH: job_id={job_id}, is_paused={is_paused}, status={response.status_code}")
                else:
                    # Создаём новую запись
                    response = await client.post(
                        f"{self.base_url}/rest/v1/scheduler_job_state",
                        headers=self._headers(),
                        json=update_data,
                        timeout=10.0,
                    )
                    print(f"[SchedulerService] POST: job_id={job_id}, is_paused={is_paused}, status={response.status_code}")

                if response.status_code not in (200, 201, 204):
                    print(f"[SchedulerService] Response: {response.text}")
                return response.status_code in (200, 201, 204)
        except Exception as e:
            print(f"[SchedulerService] set_job_paused error: {e}")
            return False

    # === Volume Stats ===

    async def record_volume_stats(self) -> bool:
        """Записать текущее количество вакансий по источникам"""
        try:
            async with httpx.AsyncClient() as client:
                # Получаем количество вакансий по источникам из vacancies_storage
                sources_count = {}

                # HH
                response = await client.get(
                    f"{self.base_url}/rest/v1/vacancies_storage",
                    params={
                        "select": "id",
                        "source": "eq.hh",
                        "is_active": "eq.true",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )
                if response.status_code in (200, 206):
                    content_range = response.headers.get("content-range", "")
                    sources_count["hh"] = int(content_range.split("/")[1]) if "/" in content_range else 0

                # SuperJob
                response = await client.get(
                    f"{self.base_url}/rest/v1/vacancies_storage",
                    params={
                        "select": "id",
                        "source": "eq.superjob",
                        "is_active": "eq.true",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )
                if response.status_code in (200, 206):
                    content_range = response.headers.get("content-range", "")
                    sources_count["superjob"] = int(content_range.split("/")[1]) if "/" in content_range else 0

                # Avito
                response = await client.get(
                    f"{self.base_url}/rest/v1/vacancies_storage",
                    params={
                        "select": "id",
                        "source": "eq.avito",
                        "is_active": "eq.true",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )
                if response.status_code in (200, 206):
                    content_range = response.headers.get("content-range", "")
                    sources_count["avito"] = int(content_range.split("/")[1]) if "/" in content_range else 0

                # Platform (employer vacancies)
                response = await client.get(
                    f"{self.base_url}/rest/v1/employer_vacancies",
                    params={
                        "select": "id",
                        "status": "eq.published",
                    },
                    headers={**self._headers(), "Prefer": "count=exact"},
                    timeout=10.0,
                )
                if response.status_code in (200, 206):
                    content_range = response.headers.get("content-range", "")
                    sources_count["platform"] = int(content_range.split("/")[1]) if "/" in content_range else 0

                # Total
                sources_count["total"] = sum(sources_count.values())

                # Записываем статистику
                now = datetime.utcnow()
                for source, count in sources_count.items():
                    await client.post(
                        f"{self.base_url}/rest/v1/vacancy_volume_stats",
                        headers=self._headers(),
                        json={
                            "recorded_at": now.isoformat(),
                            "source": source,
                            "count": count,
                        },
                        timeout=10.0,
                    )

                print(f"[SchedulerService] Volume stats recorded: {sources_count}")
                return True
        except Exception as e:
            print(f"[SchedulerService] record_volume_stats error: {e}")
            return False

    async def get_volume_history(self, hours: int = 168) -> list[dict]:
        """Получить историю объёма вакансий для графика (по умолчанию 7 дней)"""
        try:
            threshold = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/vacancy_volume_stats",
                    params={
                        "select": "*",
                        "recorded_at": f"gte.{threshold}",
                        "order": "recorded_at.asc",
                    },
                    headers=self._headers(),
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    # Группируем по времени записи
                    grouped = {}
                    for row in data:
                        recorded_at = row["recorded_at"]
                        if recorded_at not in grouped:
                            grouped[recorded_at] = {
                                "recorded_at": recorded_at,
                                "hh": 0,
                                "superjob": 0,
                                "avito": 0,
                                "platform": 0,
                                "total": 0,
                            }
                        grouped[recorded_at][row["source"]] = row["count"]

                    return list(grouped.values())
            return []
        except Exception as e:
            print(f"[SchedulerService] get_volume_history error: {e}")
            return []


# Singleton
scheduler_service = SchedulerService()
