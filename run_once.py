"""Разовый запуск для GitHub Actions (постоянно работающий бот не нужен).

  python run_once.py check            — найти новые вакансии и прислать в Telegram
  python run_once.py search "запрос"  — разовый поиск (флаги --any --raw --days N)

Нужны переменные окружения: BOT_TOKEN, CHAT_ID.
Что уже присылали, хранится в state/seen.json (его коммитит workflow).
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

import common
from sources import SOURCES, collect

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
STATE = Path("state/seen.json")
PER_MESSAGE = 10   # вакансий в одном сообщении
MAX_PUSH = 30      # максимум за один запуск; остальное придёт со следующим
KEEP_DAYS = 90


def tg(text: str) -> None:
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:  # не печатаем URL: в нём токен
        sys.exit(f"Telegram вернул {e.response.status_code}: {e.response.text}")


def keys(v):
    return ("u:" + v.url, "k:" + v.key)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text("utf-8")).get("seen", {})
    return None  # первого запуска ещё не было


def save_state(seen: dict) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    seen = {k: t for k, t in seen.items() if t >= cutoff}
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"seen": seen}, ensure_ascii=False, sort_keys=True), "utf-8")


async def fetch(query, remote, use_ex, days):
    items, errors, hidden = await collect(query, remote, use_ex, days)
    if errors:
        print("Не ответили:", ", ".join(errors))
    if len(errors) == len(SOURCES):
        sys.exit("Все источники недоступны")  # workflow упадёт, GitHub пришлёт письмо
    return items, errors, hidden


async def check():
    query, remote, use_ex, days = common.parse_args("")
    items, errors, _ = await fetch(query, remote, use_ex, days)
    seen, now = load_state(), time.time()

    if seen is None:  # первый запуск: запоминаем текущее, чат не заваливаем
        save_state({k: now for v in items for k in keys(v)})
        note = f"\n⚠️ Не ответили: {', '.join(errors)}" if errors else ""
        tg(f"✅ Слежение запущено: {common.describe(query, remote, days)}.\n"
           f"Запомнил вакансий: {len(items)}. Дальше пришлю только новые.{note}")
        return

    new = [v for v in items if not any(k in seen for k in keys(v))]
    if not new:
        print("Новых вакансий нет")
        return
    send = new[:MAX_PUSH]
    tg(f"🔔 Новых вакансий: <b>{len(new)}</b>"
       + (f" (сейчас пришлю {len(send)}, остальное — со следующей проверкой)" if len(new) > len(send) else ""))
    try:
        for i in range(0, len(send), PER_MESSAGE):
            chunk = send[i:i + PER_MESSAGE]
            tg("\n\n".join(common.format_item(i + j + 1, v) for j, v in enumerate(chunk)))
            for v in chunk:  # помечаем сразу после отправки
                for k in keys(v):
                    seen[k] = now
    finally:
        save_state(seen)


async def search(text: str):
    query, remote, use_ex, days = common.parse_args(text)
    items, errors, hidden = await fetch(query, remote, use_ex, days)
    if not items:
        return tg(f"Ничего не нашлось по {common.describe(query, remote, days)}.")
    head = f"Найдено: <b>{len(items)}</b> · {common.describe(query, remote, days)}"
    if hidden:
        head += f"\n🚫 Скрыто по минус-словам: {hidden}"
    if errors:
        head += f"\n⚠️ Не ответили: {', '.join(errors)}"
    tg(head)
    shown = items[:MAX_PUSH]
    for i in range(0, len(shown), PER_MESSAGE):
        chunk = shown[i:i + PER_MESSAGE]
        tg("\n\n".join(common.format_item(i + j + 1, v) for j, v in enumerate(chunk)))
    if len(items) > len(shown):
        tg(f"…и ещё {len(items) - len(shown)}. Уточните запрос или сократите период (--days 1).")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    asyncio.run(search(" ".join(sys.argv[2:])) if mode == "search" else check())
