import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()
import config  # noqa: E402
import db  # noqa: E402  (после load_dotenv: читает DB_PATH)
from common import describe, format_item, parse_args  # noqa: E402
from sources import Vacancy, collect  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")
PAGE = 10
cache: dict[int, list[Vacancy]] = {}  # результаты последнего поиска/уведомления по чату

bot = Bot(
    os.environ["BOT_TOKEN"],
    default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
)
dp = Dispatcher()

HELP = (
    f"<code>/search</code> — искать «{config.DEFAULT_QUERY}»"
    f"{' (удалённо)' if config.REMOTE_ONLY else ''} с вашими минус-словами.\n"
    "Другой запрос: <code>/search директор по продажам</code> или просто текстом.\n\n"
    "<b>Слежение</b>\n"
    f"<code>/watch</code> — следить за запросом и присылать только новые вакансии "
    f"(проверка каждые {config.CHECK_EVERY_MIN} мин). "
    "<code>/unwatch</code> — выключить, <code>/status</code> — что включено.\n\n"
    "Флаги: <code>--any</code> — не только удалёнка, "
    "<code>--raw</code> — без минус-слов, "
    "<code>--days 7</code> — период в днях (0 — без ограничения)."
)


def render(items: list[Vacancy], offset: int) -> tuple[str, InlineKeyboardMarkup | None]:
    chunk = items[offset:offset + PAGE]
    lines = []
    for i, v in enumerate(chunk, offset + 1):
        lines.append(format_item(i, v))
    kb = None
    if offset + PAGE < len(items):
        left = len(items) - offset - PAGE
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"Показать ещё ({left})", callback_data=f"more:{offset + PAGE}")
        ]])
    return "\n\n".join(lines), kb


# ---------- разовый поиск ----------

async def run_search(message: Message, text: str):
    query, remote, use_ex, days = parse_args(text)
    status = await message.answer(f"🔎 Ищу {describe(query, remote, days)}…")
    items, errors, hidden = await collect(query, remote, use_ex, days)
    await status.delete()

    if not items:
        note = f"\n⚠️ Не ответили: {', '.join(errors)}" if errors else ""
        if hidden:
            note += f"\n🚫 Скрыто по минус-словам: {hidden}"
        return await message.answer("Ничего не нашлось." + note)

    cache[message.chat.id] = items
    text_out, kb = render(items, 0)
    head = f"Найдено: <b>{len(items)}</b>"
    if hidden:
        head += f"\n🚫 Скрыто по минус-словам: {hidden}"
    if errors:
        head += f"\n⚠️ Не ответили: {', '.join(errors)}"
    await message.answer(head)
    await message.answer(text_out, reply_markup=kb)


@dp.message(CommandStart())
async def start(m: Message):
    await m.answer("Привет! Я ищу вакансии на нескольких сайтах сразу.\n\n" + HELP)


@dp.message(Command("search"))
async def search(m: Message, command: CommandObject):
    await run_search(m, command.args or "")


# ---------- слежение ----------

@dp.message(Command("watch"))
async def watch(m: Message, command: CommandObject):
    query, remote, use_ex, days = parse_args(command.args or "")
    status = await m.answer("⏳ Запоминаю текущие вакансии…")
    items, errors, _ = await collect(query, remote, use_ex, days)
    db.add_sub(m.chat.id, query, remote, use_ex, days)
    db.mark_seen(m.chat.id, items)  # то, что есть сейчас, повторно не пришлю
    await status.delete()
    note = f"\n⚠️ Не ответили: {', '.join(errors)}" if errors else ""
    await m.answer(
        f"✅ Слежу за {describe(query, remote, days)}.\n"
        f"Проверяю каждые {config.CHECK_EVERY_MIN} мин и присылаю только новое.\n"
        f"Сейчас запомнил вакансий: {len(items)} (посмотреть их: /search).{note}"
    )


@dp.message(Command("unwatch"))
async def unwatch(m: Message):
    await m.answer("🔕 Слежение выключено." if db.remove_sub(m.chat.id) else "Слежение и не было включено.")


@dp.message(Command("status"))
async def status_cmd(m: Message):
    sub = db.get_sub(m.chat.id)
    if not sub:
        return await m.answer("Слежение выключено. Включить: /watch")
    await m.answer(
        f"🔔 Слежу за {describe(sub['query'], bool(sub['remote']), sub['days'])}\n"
        f"Минус-слова: {'вкл' if sub['use_ex'] else 'выкл'} · "
        f"проверка каждые {config.CHECK_EVERY_MIN} мин"
    )


async def check_sub(sub):
    chat_id = sub["chat_id"]
    items, _errors, _hidden = await collect(
        sub["query"], bool(sub["remote"]), bool(sub["use_ex"]), sub["days"])
    new = db.filter_unseen(chat_id, items)
    if not new:
        return
    cache[chat_id] = new
    text, kb = render(new, 0)
    await bot.send_message(chat_id, f"🔔 Новых вакансий: <b>{len(new)}</b>")
    await bot.send_message(chat_id, text, reply_markup=kb)
    db.mark_seen(chat_id, new)  # помечаем только после успешной отправки


async def watcher():
    while True:
        for sub in db.all_subs():
            try:
                await check_sub(sub)
            except TelegramForbiddenError:  # пользователь заблокировал бота
                db.remove_sub(sub["chat_id"])
            except Exception:
                log.exception("watch check failed for chat %s", sub["chat_id"])
            await asyncio.sleep(2)  # не долбим сайты подряд
        db.prune()
        await asyncio.sleep(config.CHECK_EVERY_MIN * 60)


# ---------- прочее ----------

@dp.message(F.text & ~F.text.startswith("/"))
async def plain_text(m: Message):
    await run_search(m, m.text)


@dp.callback_query(F.data.startswith("more:"))
async def more(cb: CallbackQuery):
    items = cache.get(cb.message.chat.id)
    if not items:
        return await cb.answer("Результаты устарели, повторите поиск", show_alert=True)
    offset = int(cb.data.split(":")[1])
    text_out, kb = render(items, offset)
    await cb.message.edit_reply_markup(reply_markup=None)  # убираем старую кнопку
    await cb.message.answer(text_out, reply_markup=kb)
    await cb.answer()


async def main():
    task = asyncio.create_task(watcher())
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
