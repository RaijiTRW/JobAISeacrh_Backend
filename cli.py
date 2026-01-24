#!/usr/bin/env python3
"""
CLI для управления AI-агентами JobAISearch.

Использование:
    python cli.py status              - Статус всех агентов
    python cli.py enable <agent>      - Включить агента
    python cli.py disable <agent>     - Выключить агента
    python cli.py enable-all          - Включить всех
    python cli.py disable-all         - Выключить всех
    python cli.py usage               - Расход токенов за сегодня
    python cli.py usage --days 7      - Расход за 7 дней
    python cli.py usage --daily       - Разбивка по дням
    python cli.py reset-usage         - Сбросить статистику
"""
import argparse
import sys
from agents.agents_config import (
    AGENTS,
    get_all_agents_status,
    set_agent_enabled,
    get_usage_stats,
    get_daily_breakdown,
    reset_usage,
    is_agent_enabled,
)


# ANSI цвета
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def cmd_status(args):
    """Показать статус всех агентов."""
    agents = get_all_agents_status()

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  AI Agents Status{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")

    for agent_id, info in agents.items():
        enabled = info["enabled"]
        status = f"{GREEN}ON {RESET}" if enabled else f"{RED}OFF{RESET}"
        model = f"{DIM}({info['model_type']}){RESET}"
        print(f"  [{status}] {CYAN}{agent_id:<22}{RESET} {model}")
        print(f"        {DIM}{info['description']}{RESET}")
        print()

    enabled_count = sum(1 for a in agents.values() if a["enabled"])
    print(f"  {DIM}Всего: {enabled_count}/{len(agents)} включено{RESET}\n")


def cmd_enable(args):
    """Включить агента."""
    agent_id = args.agent
    if agent_id not in AGENTS:
        print(f"{RED}Ошибка: неизвестный агент '{agent_id}'{RESET}")
        print(f"Доступные: {', '.join(AGENTS.keys())}")
        sys.exit(1)

    set_agent_enabled(agent_id, True)
    print(f"{GREEN}[ON]{RESET} {agent_id} — включён")


def cmd_disable(args):
    """Выключить агента."""
    agent_id = args.agent
    if agent_id not in AGENTS:
        print(f"{RED}Ошибка: неизвестный агент '{agent_id}'{RESET}")
        print(f"Доступные: {', '.join(AGENTS.keys())}")
        sys.exit(1)

    set_agent_enabled(agent_id, False)
    print(f"{RED}[OFF]{RESET} {agent_id} — выключен")


def cmd_enable_all(args):
    """Включить всех агентов."""
    for agent_id in AGENTS:
        set_agent_enabled(agent_id, True)
    print(f"{GREEN}Все агенты включены{RESET}")


def cmd_disable_all(args):
    """Выключить всех агентов."""
    for agent_id in AGENTS:
        set_agent_enabled(agent_id, False)
    print(f"{RED}Все агенты выключены{RESET}")


def cmd_usage(args):
    """Показать расход токенов."""
    days = args.days

    if args.daily:
        # Разбивка по дням
        breakdown = get_daily_breakdown(days)
        print(f"\n{BOLD}{'='*50}{RESET}")
        print(f"{BOLD}  Token Usage - Daily Breakdown ({days} days){RESET}")
        print(f"{BOLD}{'='*50}{RESET}\n")

        total_cost = 0.0
        for day_info in breakdown:
            cost = day_info["cost_usd"]
            total_cost += cost
            calls = day_info["calls"]
            bar = "█" * min(int(cost * 10), 40)  # Визуальная шкала
            cost_color = RED if cost > 1.0 else YELLOW if cost > 0.3 else GREEN
            print(f"  {day_info['date']}  {cost_color}${cost:.4f}{RESET}  {DIM}{calls:>4} calls{RESET}  {CYAN}{bar}{RESET}")

        print(f"\n  {BOLD}Итого: ${total_cost:.4f}{RESET}\n")
        return

    # Подробная статистика
    stats = get_usage_stats(days)
    agents_stats = stats["agents"]
    total_cost = stats["total_cost_usd"]

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  Token Usage {'(today)' if days == 1 else f'(last {days} days)'}{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    if not agents_stats:
        print(f"  {DIM}Нет данных за этот период{RESET}\n")
        return

    # Заголовок таблицы
    print(f"  {BOLD}{'Agent':<22} {'Calls':>6} {'Input':>10} {'Output':>10} {'Cost USD':>10}{RESET}")
    print(f"  {'-'*64}")

    # Сортируем по стоимости
    sorted_agents = sorted(agents_stats.items(), key=lambda x: x[1]["cost_usd"], reverse=True)

    for agent_id, data in sorted_agents:
        cost = data["cost_usd"]
        cost_color = RED if cost > 1.0 else YELLOW if cost > 0.3 else GREEN
        input_k = f"{data['input_tokens']/1000:.1f}k"
        output_k = f"{data['output_tokens']/1000:.1f}k"

        print(
            f"  {CYAN}{agent_id:<22}{RESET} "
            f"{data['calls']:>6} "
            f"{input_k:>10} "
            f"{output_k:>10} "
            f"{cost_color}${cost:.4f}{RESET}"
        )

    print(f"  {'-'*64}")

    total_color = RED if total_cost > 3.0 else YELLOW if total_cost > 1.0 else GREEN
    print(f"  {BOLD}{'TOTAL':<22} {'':>6} {'':>10} {'':>10} {total_color}${total_cost:.4f}{RESET}")
    print()

    # Прогноз на месяц
    if days == 1 and total_cost > 0:
        monthly = total_cost * 30
        monthly_color = RED if monthly > 50 else YELLOW if monthly > 20 else GREEN
        print(f"  {DIM}Прогноз на месяц: {monthly_color}~${monthly:.2f}{RESET}")
        print()


def cmd_reset_usage(args):
    """Сбросить статистику."""
    if args.older_than:
        reset_usage(days=args.older_than)
        print(f"{YELLOW}Статистика старше {args.older_than} дней удалена{RESET}")
    else:
        confirm = input(f"{YELLOW}Удалить ВСЮ статистику? (y/N): {RESET}")
        if confirm.lower() == 'y':
            reset_usage()
            print(f"{GREEN}Статистика сброшена{RESET}")
        else:
            print("Отменено")


def main():
    parser = argparse.ArgumentParser(
        description="CLI управления AI-агентами JobAISearch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Агенты:
  content_moderator    Модерация вакансий (военные, мошенничество)
  employer_moderator   Модерация вакансий работодателей
  crew_analyst         Анализ запроса пользователя
  crew_strategist      Генерация поисковых запросов
  crew_validator       Валидация найденных вакансий
  crew_composer        Составление ответа

Примеры:
  python cli.py status
  python cli.py disable content_moderator
  python cli.py usage --days 7
  python cli.py usage --daily
  python cli.py disable crew_validator
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Команда")

    # status
    subparsers.add_parser("status", help="Статус всех агентов")

    # enable
    p_enable = subparsers.add_parser("enable", help="Включить агента")
    p_enable.add_argument("agent", help="ID агента")

    # disable
    p_disable = subparsers.add_parser("disable", help="Выключить агента")
    p_disable.add_argument("agent", help="ID агента")

    # enable-all
    subparsers.add_parser("enable-all", help="Включить всех агентов")

    # disable-all
    subparsers.add_parser("disable-all", help="Выключить всех агентов")

    # usage
    p_usage = subparsers.add_parser("usage", help="Расход токенов")
    p_usage.add_argument("--days", type=int, default=1, help="За сколько дней (default: 1)")
    p_usage.add_argument("--daily", action="store_true", help="Разбивка по дням")

    # reset-usage
    p_reset = subparsers.add_parser("reset-usage", help="Сбросить статистику")
    p_reset.add_argument("--older-than", type=int, help="Удалить старше N дней")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "status": cmd_status,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "enable-all": cmd_enable_all,
        "disable-all": cmd_disable_all,
        "usage": cmd_usage,
        "reset-usage": cmd_reset_usage,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
