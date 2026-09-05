#!/usr/bin/env python3
"""
Drebolbot: первичная настройка.

Пишет .env и создаёт стартовые файлы данных. Может быть вызван:
  - из install.sh с готовыми значениями через флаги (без лишних вопросов),
  - напрямую (`python3 install.py`) — тогда недостающие значения спросит сам.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"


def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def ok(label: str, value: str = "") -> None:
    suffix = f" {DIM}{value}{RESET}" if value else ""
    print(f"  {GREEN}✓{RESET} {label}{suffix}")


def ask(prompt: str, default: str | None = None, required: bool = False, secret: bool = False) -> str:
    suffix = f" {DIM}[{default}]{RESET}" if default else ""
    while True:
        value = input(f"  {c('›', CYAN)} {prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value:
            return value
        if not required:
            return ""
        print(f"  {c('Это поле обязательно.', YELLOW)}")


def write_env(items: dict[str, str]) -> None:
    ENV_FILE.write_text(
        "\n".join(f"{key}={value}" for key, value in items.items()) + "\n",
        encoding="utf-8",
    )


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def main() -> None:
    parser = argparse.ArgumentParser(description="Drebolbot: первичная настройка")
    parser.add_argument("--token", help="Токен Telegram-бота")
    parser.add_argument("--admin-id", help="Telegram ID администратора")
    parser.add_argument("--domain", help="Домен веб-панели")
    parser.add_argument("--web-username", help="Логин веб-панели")
    parser.add_argument("--web-password", help="Пароль веб-панели")
    args = parser.parse_args()

    print()
    print(c("  ⚡ Drebolbot — первичная настройка", BOLD))
    print(c("  ─────────────────────────────────", DIM))
    print()

    domain = args.domain or ask("Домен сайта (например panel.example.com)", required=True)
    web_username = args.web_username or ask("Логин веб-панели", default="admin")
    web_password = args.web_password or ask("Пароль веб-панели", required=True)
    token = args.token or ask("Токен Telegram-бота (от @BotFather)", required=True)
    admin_id = args.admin_id or ask("Telegram ID администратора", required=True)

    ROOT.mkdir(parents=True, exist_ok=True)
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    env = {
        "TOKEN": token,
        "ADMIN_ID": admin_id,
        "DOMAIN": domain,
        "WEB_USERNAME": web_username,
        "WEB_PASSWORD": web_password,
        "AUTH_FILE": "data/authorized.json",
        "FP_TOKEN": "",
        "DATA_DIR": "data",
        "INVENTORY_FILE": "data/inventory.json",
        "GROQ_API_KEY": "",
        "OPENROUTER_API_KEY": "",
    }
    write_env(env)

    inv = data_dir / "inventory.json"
    if not inv.exists():
        inv.write_text(json.dumps({}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    auth_path = ROOT / env["AUTH_FILE"]
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    if not auth_path.exists():
        auth_path.write_text("[]\n", encoding="utf-8")

    print()
    ok("Конфигурация записана", str(ENV_FILE))
    ok("Домен", domain)
    ok("Веб-логин", web_username)
    ok("Веб-пароль", mask(web_password))
    ok("Telegram-бот настроен", f"ADMIN_ID={admin_id}")
    ok("data/inventory.json готов")
    ok("data/authorized.json готов")
    print()


if __name__ == "__main__":
    main()
