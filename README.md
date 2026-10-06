# Vacancy Bot

Telegram-бот, который по запросу ищет вакансии сразу на нескольких площадках,
убирает дубли и присылает результат с постраничной выдачей.

## Источники

| Источник    | Доступ                                  |
|-------------|-----------------------------------------|
| hh.ru       | открытое API                            |
| Habr Career | внутренний JSON сайта (неофициальный)   |
| SuperJob    | API, нужен ключ `SUPERJOB_KEY`          |

## Быстрый старт

```bash
git clone https://github.com/<ваш-логин>/vacancy-bot.git
cd vacancy-bot
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # впишите BOT_TOKEN и APP_UA
python bot.py
```

Токен бота выдаёт [@BotFather](https://t.me/BotFather).

## Использование

- `/search` — запрос по умолчанию из `config.py` (удалённо, с минус-словами)
- `/search директор по продажам` или просто текстом — другой запрос
- `/watch` — следить за запросом: бот сам проверяет источники и присылает только новые вакансии; `/unwatch` — выключить, `/status` — что включено
- `--any` — не только удалёнка, `--raw` — без минус-слов
- `--days 7` — период публикации в днях (по умолчанию `FRESH_DAYS` из `config.py`, `0` — без ограничения)

## Круглосуточная работа на GitHub Actions (без включённого компьютера)

GitHub не умеет держать запущенного бота, но умеет запускать скрипт по расписанию.
Workflow `.github/workflows/vacancies.yml` раз в час ищет новые вакансии и присылает их в Telegram
(`run_once.py`). Команды `/search` и `/watch` в этом режиме не работают.

1. Выложите репозиторий на GitHub.
2. *Settings → Secrets and variables → Actions → New repository secret*:
   `BOT_TOKEN`, `CHAT_ID` (ваш id в Telegram, узнать у @userinfobot);
   необязательно `SUPERJOB_KEY`, `APP_UA`.
3. *Settings → Actions → General → Workflow permissions → Read and write*.
4. Вкладка *Actions → Vacancy check → Run workflow* — первый запуск запоминает текущие вакансии,
   дальше приходят только новые.
5. Разовый поиск: *Run workflow* и впишите запрос (например `директор по продажам --days 7`).

## Настройка

Все параметры (запрос по умолчанию, удалёнка, минус-слова) лежат в `config.py`.

## Структура

```
bot.py        # Telegram-бот (aiogram 3)
config.py     # ваши параметры поиска и минус-слова
db.py         # SQLite: подписка и уже показанные вакансии
sources.py    # сборщики вакансий, нормализация, дедупликация
```

Новый источник: напишите `async def fetch_xxx(client, query, remote)` в `sources.py`,
верните список `Vacancy` и добавьте функцию в словарь `SOURCES`.

## Важно

- Соблюдайте условия использования сайтов и не делайте запросы слишком часто.
- Не коммитьте `.env`: он уже в `.gitignore`.

## Лицензия

MIT
