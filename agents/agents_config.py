"""
Менеджер конфигурации агентов.
Управляет вкл/выкл агентов и отслеживает расход токенов.
"""
import json
import os
from datetime import datetime, date, timedelta
from typing import Optional
from pathlib import Path

# Путь к файлу конфигурации (рядом с main.py)
CONFIG_DIR = Path(__file__).parent.parent / "data"
CONFIG_FILE = CONFIG_DIR / "agents_config.json"
USAGE_FILE = CONFIG_DIR / "agents_usage.json"

# Цены моделей (USD за 1M токенов) - OpenRouter pricing
MODEL_PRICING = {
    "anthropic/claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "anthropic/claude-sonnet-4.5": {"input": 3.0, "output": 15.0},
    "anthropic/claude-sonnet-4-5-20250514": {"input": 3.0, "output": 15.0},
    "anthropic/claude-3.5-sonnet": {"input": 3.0, "output": 15.0},
    "anthropic/claude-3-haiku": {"input": 0.25, "output": 1.25},
    "anthropic/claude-3-5-haiku": {"input": 0.8, "output": 4.0},
}

# Все агенты и их описания
AGENTS = {
    "content_moderator": {
        "name": "Content Moderator",
        "description": "Модерация вакансий в ленте (убирает военные, мошенничество)",
        "model_type": "main",  # использует основную модель
    },
    "employer_moderator": {
        "name": "Employer Vacancy Moderator",
        "description": "Модерация вакансий работодателей при публикации",
        "model_type": "main",
    },
    "crew_analyst": {
        "name": "CrewAI Analyst",
        "description": "Анализ запроса пользователя, определение типа (SEARCH/CLARIFICATION/CHAT)",
        "model_type": "main",
    },
    "crew_strategist": {
        "name": "CrewAI Strategist",
        "description": "Генерация поисковых запросов и стратегии поиска",
        "model_type": "fast",
    },
    "crew_validator": {
        "name": "CrewAI Validator",
        "description": "Валидация и фильтрация найденных вакансий",
        "model_type": "fast",
    },
    "crew_composer": {
        "name": "CrewAI Composer",
        "description": "Составление текстового ответа пользователю",
        "model_type": "main",
    },
    "support_chat": {
        "name": "Support Chat",
        "description": "AI-чат поддержки (FloatingChat на сайте)",
        "model_type": "main",
    },
}


def _ensure_dirs():
    """Создаёт директорию data если не существует."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_config() -> dict:
    """Загрузить конфигурацию агентов."""
    _ensure_dirs()
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    # Дефолт: все агенты включены
    default = {agent_id: True for agent_id in AGENTS}
    _save_config(default)
    return default


def _save_config(config: dict):
    """Сохранить конфигурацию."""
    _ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _load_usage() -> dict:
    """Загрузить статистику использования."""
    _ensure_dirs()
    if USAGE_FILE.exists():
        with open(USAGE_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_usage(usage: dict):
    """Сохранить статистику."""
    _ensure_dirs()
    with open(USAGE_FILE, "w") as f:
        json.dump(usage, f, indent=2, default=str)


def is_agent_enabled(agent_id: str) -> bool:
    """Проверить, включён ли агент."""
    config = _load_config()
    return config.get(agent_id, True)


def set_agent_enabled(agent_id: str, enabled: bool):
    """Включить/выключить агента."""
    if agent_id not in AGENTS:
        raise ValueError(f"Unknown agent: {agent_id}. Available: {list(AGENTS.keys())}")
    config = _load_config()
    config[agent_id] = enabled
    _save_config(config)


def get_all_agents_status() -> dict:
    """Получить статус всех агентов."""
    config = _load_config()
    result = {}
    for agent_id, info in AGENTS.items():
        result[agent_id] = {
            **info,
            "enabled": config.get(agent_id, True),
        }
    return result


def track_usage(agent_id: str, model: str, input_tokens: int, output_tokens: int):
    """Записать использование токенов агентом."""
    usage = _load_usage()
    today = date.today().isoformat()

    if today not in usage:
        usage[today] = {}

    if agent_id not in usage[today]:
        usage[today][agent_id] = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "models_used": {},
        }

    entry = usage[today][agent_id]
    entry["calls"] += 1
    entry["input_tokens"] += input_tokens
    entry["output_tokens"] += output_tokens

    # Трекаем модели
    if model not in entry["models_used"]:
        entry["models_used"][model] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    entry["models_used"][model]["input_tokens"] += input_tokens
    entry["models_used"][model]["output_tokens"] += output_tokens
    entry["models_used"][model]["calls"] += 1

    _save_usage(usage)


def get_usage_stats(days: int = 1) -> dict:
    """Получить статистику использования за N дней."""
    usage = _load_usage()
    today = date.today()

    stats = {}
    total_cost = 0.0

    for i in range(days):
        day = (today - timedelta(days=i)).isoformat()
        if day in usage:
            for agent_id, data in usage[day].items():
                if agent_id not in stats:
                    stats[agent_id] = {
                        "name": AGENTS.get(agent_id, {}).get("name", agent_id),
                        "calls": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_usd": 0.0,
                    }

                stats[agent_id]["calls"] += data["calls"]
                stats[agent_id]["input_tokens"] += data["input_tokens"]
                stats[agent_id]["output_tokens"] += data["output_tokens"]

                # Считаем стоимость
                for model, model_data in data.get("models_used", {}).items():
                    pricing = _get_pricing(model)
                    input_cost = (model_data["input_tokens"] / 1_000_000) * pricing["input"]
                    output_cost = (model_data["output_tokens"] / 1_000_000) * pricing["output"]
                    cost = input_cost + output_cost
                    stats[agent_id]["cost_usd"] += cost
                    total_cost += cost

    return {"agents": stats, "total_cost_usd": total_cost, "days": days}


def get_daily_breakdown(days: int = 7) -> list:
    """Получить разбивку по дням."""
    usage = _load_usage()
    today = date.today()
    breakdown = []

    for i in range(days):
        day = (today - timedelta(days=i)).isoformat()
        day_data = usage.get(day, {})
        day_cost = 0.0
        day_calls = 0

        for agent_id, data in day_data.items():
            day_calls += data["calls"]
            for model, model_data in data.get("models_used", {}).items():
                pricing = _get_pricing(model)
                input_cost = (model_data["input_tokens"] / 1_000_000) * pricing["input"]
                output_cost = (model_data["output_tokens"] / 1_000_000) * pricing["output"]
                day_cost += input_cost + output_cost

        breakdown.append({
            "date": day,
            "calls": day_calls,
            "cost_usd": round(day_cost, 4),
        })

    return breakdown


def _get_pricing(model: str) -> dict:
    """Получить цены для модели."""
    # Точное совпадение
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # Частичное совпадение
    for key, pricing in MODEL_PRICING.items():
        if key in model or model in key:
            return pricing
    # Дефолт - sonnet pricing
    return {"input": 3.0, "output": 15.0}


def reset_usage(days: Optional[int] = None):
    """Сбросить статистику. days=None — всю, days=N — старше N дней."""
    if days is None:
        _save_usage({})
        return

    usage = _load_usage()
    today = date.today()
    cutoff = (today - timedelta(days=days)).isoformat()

    # Удаляем старые записи
    usage = {day: data for day, data in usage.items() if day >= cutoff}
    _save_usage(usage)
