"""Источники вакансий. Каждый сборщик возвращает list[Vacancy].
Чтобы добавить новый источник: напишите async-функцию и внесите её в SOURCES."""
import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

import config

log = logging.getLogger("sources")
UA = os.getenv("APP_UA") or "VacancyBot/1.0 (you@example.com)"


@dataclass
class Vacancy:
    title: str
    company: str
    salary: str
    city: str
    url: str
    source: str
    snippet: str = ""
    published: Optional[datetime] = None

    @property
    def key(self) -> str:
        """Ключ для дедупликации: одна вакансия на разных площадках."""
        norm = lambda s: re.sub(r"\W+", "", (s or "").lower())
        return norm(self.title) + "|" + norm(self.company)


def _money(a, b, cur) -> str:
    parts = []
    if a:
        parts.append(f"от {a:,}".replace(",", " "))
    if b:
        parts.append(f"до {b:,}".replace(",", " "))
    return (" ".join(parts) + f" {cur or ''}").strip() or "не указана"


def _ok(r: httpx.Response) -> None:
    """Ошибка с кодом и началом ответа сайта (видно в логах Actions)."""
    if r.is_error:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")


async def fetch_hh(client: httpx.AsyncClient, query: str, remote: bool, days: int = 0):
    params = {"text": query, "per_page": 50, "order_by": "publication_time"}
    if config.TITLE_ONLY:
        params["search_field"] = "name"
    if days:
        params["period"] = min(days, 30)  # hh.ru: максимум 30 дней
    # hh.ru менял название фильтра удалёнки: пробуем новое, затем старое
    variants = [{"work_format": "REMOTE"}, {"schedule": "remote"}] if remote else [{}]
    for extra in variants:
        r = await client.get("https://api.hh.ru/vacancies", params={**params, **extra},
                             headers={"HH-User-Agent": UA})
        if r.status_code != 400:
            break
    _ok(r)
    out = []
    for it in (r.json().get("items") or []):
        s = it.get("salary") or {}
        out.append(Vacancy(
            title=it["name"],
            company=(it.get("employer") or {}).get("name", ""),
            salary=_money(s.get("from"), s.get("to"), s.get("currency")),
            city=(it.get("area") or {}).get("name", ""),
            url=it["alternate_url"],
            source="hh.ru",
            snippet=_clean(" ".join(filter(None, (it.get("snippet") or {}).values()))),
            published=_parse_dt(it.get("published_at")),
        ))
    return out


async def fetch_habr(client: httpx.AsyncClient, query: str, remote: bool, days: int = 0):
    # Внутренний JSON-эндпоинт сайта: он неофициальный и может измениться
    params = {"q": query, "type": "all", "sort": "date"}
    if remote:
        params["remote"] = "true"
    r = await client.get("https://career.habr.com/api/frontend/vacancies", params=params)
    _ok(r)
    out = []
    for it in (r.json().get("list") or []):
        out.append(Vacancy(
            title=it.get("title", ""),
            company=(it.get("company") or {}).get("title", ""),
            salary=(it.get("salary") or {}).get("formatted") or "не указана",
            city=", ".join(filter(None, (l.get("title") for l in (it.get("locations") or [])))) or
                 ("Удалённо" if it.get("remoteWork") else ""),
            url="https://career.habr.com" + it.get("href", ""),
            source="Habr Career",
            published=_parse_dt((it.get("publishedDate") or {}).get("date")),
        ))
    return out


async def fetch_superjob(client: httpx.AsyncClient, query: str, remote: bool, days: int = 0):
    key = os.getenv("SUPERJOB_KEY")
    if not key:
        return []
    kw = query + (" удалённо" if remote else "")
    params = {"keyword": kw, "count": 50}
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        params["date_published_from"] = int(since.timestamp())
    r = await client.get("https://api.superjob.ru/2.0/vacancies/",
                         params=params,
                         headers={"X-Api-App-Id": key})
    _ok(r)
    return [Vacancy(
        title=it.get("profession", ""),
        company=it.get("firm_name", ""),
        salary=_money(it.get("payment_from"), it.get("payment_to"), it.get("currency")),
        city=(it.get("town") or {}).get("title", ""),
        url=it.get("link", ""),
        source="SuperJob",
        published=(datetime.fromtimestamp(it["date_published"], timezone.utc)
                   if it.get("date_published") else None),
    ) for it in (r.json().get("objects") or [])]


SOURCES = {"hh.ru": fetch_hh, "Habr Career": fetch_habr}
if os.getenv("SUPERJOB_KEY"):  # без ключа SuperJob не подключаем
    SOURCES["SuperJob"] = fetch_superjob


def _parse_dt(s) -> Optional[datetime]:
    """Разбирает дату из ответа сайта; None, если формат неизвестен."""
    if not s:
        return None
    for parse in (lambda x: datetime.strptime(x, "%Y-%m-%dT%H:%M:%S%z"), datetime.fromisoformat):
        try:
            d = parse(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _clean(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "")


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def _compile_excludes(words):
    pats = []
    for w in words:
        w = _norm(w).strip()
        if not w:
            continue
        e = re.escape(w)
        pats.append(rf"\b{e}\b" if len(w) <= 3 else rf"\b{e}")
    return re.compile("|".join(pats)) if pats else None


_EXCLUDE_RE = _compile_excludes(config.EXCLUDE_WORDS)


def is_excluded(v: Vacancy) -> bool:
    """True, если в вакансии есть минус-слово."""
    if not _EXCLUDE_RE:
        return False
    text = f"{v.title} {v.company}"
    if config.EXCLUDE_SCOPE == "all":
        text += " " + v.snippet
    return bool(_EXCLUDE_RE.search(_norm(text)))


async def collect(query: str, remote: bool = False, use_excludes: bool = True, days: int = 0):
    """Опрашивает все источники параллельно. Возвращает (вакансии, ошибки, скрыто)."""
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}) as client:
        results = await asyncio.gather(
            *(fn(client, query, remote, days) for fn in SOURCES.values()),
            return_exceptions=True,
        )
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    vacancies, errors, seen, hidden = [], [], set(), 0
    for name, res in zip(SOURCES, results):
        if isinstance(res, Exception):
            log.warning("%s failed: %r", name, res)
            errors.append(name)
            continue
        for v in res:
            # страховка: даже если сайт не умеет фильтр по дате, старое отсекаем сами
            if cutoff and v.published and v.published < cutoff:
                continue
            if use_excludes and is_excluded(v):
                hidden += 1
                continue
            if v.key not in seen:  # дедупликация
                seen.add(v.key)
                vacancies.append(v)
    # свежие сверху; вакансии без даты — в конец
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    vacancies.sort(key=lambda v: v.published or epoch, reverse=True)
    return vacancies, errors, hidden
