"""Разовый запуск для GitHub Actions (постоянно работающий бот не нужен).

  python run_once.py check            — найти новые вакансии и прислать в Telegram
  python run_once.py search "запрос"  — разовый поиск (флаги --any --raw --days N)

Нужны переменные окружения: BOT_TOKEN, CHAT_ID.
Что уже присылали и какие источники сейчас не отвечают — в state/seen.json
(его коммитит workflow).
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
    """None — первого запуска ещё не было."""
    if not STATE.exists():
        return None
    d = json.loads(STATE.read_text("utf-8"))
    return {"seen": d.get("seen", {}), "failing": d.get("failing", [])}


def save_state(st: dict) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    st["seen"] = {k: t for k, t in st["seen"].items() if t >= cutoff}
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, sort_keys=True), "utf-8")


async def fetch(query, remote, use_ex, days):
    items, errors, hidden = await collect(query, remote, use_ex, days)
    if errors:
        print("Не ответили:", ", ".join(errors))
    return items, errors, hidden


def notify_source_changes(st: dict, errors: list) -> None:
    """Пишем в Telegram только когда источник сломался или снова заработал."""
    prev, now_failing = set(st["failing"]), set(errors)
    if now_failing - prev:
        tg(f"⚠️ Не отвечают источники: {', '.join(sorted(now_failing - prev))}. "
           "Подробности в логах запуска на GitHub (Actions).")
    if prev - now_failing:
        tg(f"✅ Снова работают: {', '.join(sorted(prev - now_failing))}")
    st["failing"] = sorted(now_failing)


async def check():
    query, remote, use_ex, days = common.parse_args("")
    items, errors, _ = await fetch(query, remote, use_ex, days)
    st, now = load_state(), time.time()

    if st is None and len(errors) >= len(SOURCES):  # ничего не получили — базовый список не фиксируем
        return tg("⚠️ Слежение не запустилось: ни один источник не ответил. "
                  "Подробности в логах запуска на GitHub (Actions).")

    if st is None:  # первый запуск: запоминаем текущее, чат не заваливаем
        save_state({"seen": {k: now for v in items for k in keys(v)}, "failing": sorted(errors)})
        note = f"\n⚠️ Не ответили: {', '.join(errors)}" if errors else ""
        tg(f"✅ Слежение запущено: {common.describe(query, remote, days)}.\n"
           f"Запомнил вакансий: {len(items)}. Дальше пришлю только новые.{note}")
        return

    notify_source_changes(st, errors)
    new = [v for v in items if not any(k in st["seen"] for k in keys(v))]
    try:
        if not new:
            print("Новых вакансий нет")
            return
        send = new[:MAX_PUSH]
        tg(f"🔔 Новых вакансий: <b>{len(new)}</b>"
           + (f" (сейчас пришлю {len(send)}, остальное — со следующей проверкой)" if len(new) > len(send) else ""))
        for i in range(0, len(send), PER_MESSAGE):
            chunk = send[i:i + PER_MESSAGE]
            tg("\n\n".join(common.format_item(i + j + 1, v) for j, v in enumerate(chunk)))
            for v in chunk:  # помечаем сразу после отправки
                for k in keys(v):
                    st["seen"][k] = now
    finally:
        save_state(st)


async def search(text: str):
    query, remote, use_ex, days = common.parse_args(text)
    items, errors, hidden = await fetch(query, remote, use_ex, days)
    if not items:
        return tg(f"Ничего не нашлось по {common.describe(query, remote, days)}."
                  + (f"\n⚠️ Не ответили: {', '.join(errors)}" if errors else ""))
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
