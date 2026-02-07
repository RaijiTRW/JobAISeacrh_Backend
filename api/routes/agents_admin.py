"""
API для управления агентами (аналог CLI).
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from agents.agents_config import (
    get_all_agents_status,
    set_agent_enabled,
    get_usage_stats,
    get_daily_breakdown,
    reset_usage,
    AGENTS,
)

router = APIRouter(prefix="/api/admin/agents", tags=["agents-admin"])


class AgentToggleRequest(BaseModel):
    agent_id: str
    enabled: bool


@router.get("/status")
async def agents_status():
    """Статус всех агентов."""
    return get_all_agents_status()


@router.post("/toggle")
async def toggle_agent(request: AgentToggleRequest):
    """Включить/выключить агента."""
    agent_id = request.agent_id
    enabled = request.enabled

    if agent_id not in AGENTS:
        print(f"\n{'='*60}")
        print(f"[AGENTS ERROR] Неизвестный агент: {agent_id}")
        print(f"  Доступные агенты: {list(AGENTS.keys())}")
        print(f"{'='*60}\n")
        return {"error": f"Unknown agent: {agent_id}", "available": list(AGENTS.keys())}

    agent_info = AGENTS[agent_id]
    agent_name = agent_info.get("name", agent_id)
    action = "ВКЛЮЧИТЬ" if enabled else "ВЫКЛЮЧИТЬ"

    print(f"\n{'='*60}")
    print(f"[AGENTS {action}] {agent_name} ({agent_id})")
    print(f"  Описание: {agent_info.get('description', 'N/A')}")
    print(f"  Действие: {'Агент ВКЛЮЧЁН' if enabled else 'Агент ВЫКЛЮЧЕН'}")
    print(f"{'='*60}\n")

    set_agent_enabled(agent_id, enabled)

    return {"agent_id": agent_id, "enabled": enabled}


@router.get("/usage")
async def agents_usage(days: int = 1):
    """Расход токенов."""
    return get_usage_stats(days)


@router.get("/usage/daily")
async def agents_usage_daily(days: int = 7):
    """Разбивка по дням."""
    return {"breakdown": get_daily_breakdown(days)}


@router.post("/usage/reset")
async def agents_reset_usage(older_than: Optional[int] = None):
    """Сбросить статистику."""
    reset_usage(days=older_than)
    return {"status": "ok", "older_than": older_than}
