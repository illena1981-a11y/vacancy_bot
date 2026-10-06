"""Общие функции: разбор флагов запроса и оформление вакансий (бот и GitHub Actions)."""
import re
from datetime import datetime, timezone
from html import escape

import config


def parse_args(text: str):
    """Текст команды -> (запрос, удалёнка, минус-слова, дни)."""
    remote = "--remote" in text or (config.REMOTE_ONLY and "--any" not in text)
    use_ex = "--raw" not in text
    m = re.search(r"--days\s+(\d+)", text)
    days = int(m.group(1)) if m else config.FRESH_DAYS
    text = re.sub(r"--days\s+\d+", "", text)
    for flag in ("--remote", "--any", "--raw"):
        text = text.replace(flag, "")
    return (text.strip() or config.DEFAULT_QUERY), remote, use_ex, days


def describe(query: str, remote: bool, days: int) -> str:
    return f"«{escape(query)}»{' (удалёнка)' if remote else ''}{f', за {days} дн.' if days else ''}"


def ago(dt) -> str:
    if not dt:
        return "дата не указана"
    h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if h < 1:
        return "только что"
    if h < 24:
        return f"{int(h)} ч назад"
    return f"{int(h // 24)} дн. назад"


def format_item(i: int, v) -> str:
    return (
        f"<b>{i}. <a href=\"{escape(v.url)}\">{escape(v.title)}</a></b>\n"
        f"🏢 {escape(v.company or '—')} · 📍 {escape(v.city or '—')}\n"
        f"💰 {escape(v.salary)} · <i>{v.source}</i> · 🕒 {ago(v.published)}"
    )
