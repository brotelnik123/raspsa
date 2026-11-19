import json
import os
import asyncio
from datetime import datetime, timedelta, time
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------- Загрузка расписания ----------
BASE_DIR = Path(__file__).resolve().parent
SCHEDULE_PATH = BASE_DIR / "schedule.json"

with SCHEDULE_PATH.open(encoding="utf-8") as f:
    SCHEDULE = json.load(f)

GROUPS = list(SCHEDULE.keys())

# Хранилище выбранной группы: chat_id -> название группы
# (в реальном проекте лучше использовать persistence, но для учебного сойдёт)
user_group: dict[int, str] = {}

WEEKDAYS_RU = [
    "Понедельник", "Вторник", "Среда",
    "Четверг", "Пятница", "Суббота", "Воскресенье",
]


def get_today_key() -> str:
    return WEEKDAYS_RU[datetime.now().weekday()]


def get_tomorrow_key() -> str:
    return WEEKDAYS_RU[(datetime.now() + timedelta(days=1)).weekday()]


def format_pair(pair: dict, index: int) -> str:
    """Форматирует одну пару в читаемый текст."""
    lines = [f"*{index}.* {pair['subject']}"]
    lines.append(f"⏰ {pair['start']}–{pair['end']}")
    if "half1_start" in pair:
        lines.append(
            f"   1-я полупара: {pair['half1_start']}–{pair['half1_end']}\n"
            f"   2-я полупара: {pair['half2_start']}–{pair['half2_end']}"
        )
    lines.append(f"🚪 {pair['room']}")
    return "\n".join(lines)


def format_day(group: str, day_key: str) -> str:
    """Возвращает текст расписания на день для группы."""
    if group not in SCHEDULE:
        return "Группа не найдена."
    day_pairs = SCHEDULE[group].get(day_key)
    if not day_pairs:
        return f"📅 *{day_key}* — пар нет."

    lines = [f"📅 *{day_key}* — группа *{group}*\n"]
    for i, pair in enumerate(day_pairs, start=1):
        lines.append(format_pair(pair, i))
        lines.append("")  # пустая строка между парами
    return "\n".join(lines).strip()


def parse_pair_start(pair: dict) -> datetime:
    """Возвращает datetime начала пары на сегодня."""
    today = datetime.now().date()
    h, m = map(int, pair["start"].split(":"))
    return datetime.combine(today, time(h, m))


def get_current_and_next_pairs(group: str):
    """Возвращает (текущая_пара_или_None, следующая_пара_или_None) на сегодня."""
    today_key = get_today_key()
    day_pairs = SCHEDULE.get(group, {}).get(today_key, [])
    if not day_pairs:
        return None, None

    now = datetime.now()
    current = None
    next_pair = None

    for pair in day_pairs:
        start_dt = parse_pair_start(pair)
        h, m = map(int, pair["end"].split(":"))
        end_dt = datetime.combine(now.date(), time(h, m))

        if start_dt <= now <= end_dt:
            current = pair
        elif start_dt > now and next_pair is None:
            next_pair = pair
            break

    return current, next_pair


# ---------- Обработчики команд ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(g, callback_data=f"group:{g}")] for g in GROUPS
    ]
    await update.message.reply_text(
        "Привет! Выбери свою группу:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def choose_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    group = query.data.split(":", 1)[1]
    user_group[query.message.chat_id] = group
    await query.edit_message_text(
        f"Группа *{group}* сохранена. Теперь доступны команды:\n"
        f"/today — расписание на сегодня\n"
        f"/next — следующая пара\n"
        f"/tomorrow — расписание на завтра",
        parse_mode="Markdown",
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    group = user_group.get(chat_id)
    if not group:
        await update.message.reply_text("Сначала выбери группу командой /start")
        return
    text = format_day(group, get_today_key())
    await update.message.reply_text(text, parse_mode="Markdown")


async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    group = user_group.get(chat_id)
    if not group:
        await update.message.reply_text("Сначала выбери группу командой /start")
        return
    text = format_day(group, get_tomorrow_key())
    await update.message.reply_text(text, parse_mode="Markdown")


async def next_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    group = user_group.get(chat_id)
    if not group:
        await update.message.reply_text("Сначала выбери группу командой /start")
        return

    _, next_p = get_current_and_next_pairs(group)
    if next_p is None:
        await update.message.reply_text(
            "На сегодня пар больше нет (или расписание не задано)."
        )
        return

    now = datetime.now()
    start_dt = parse_pair_start(next_p)
    delta = start_dt - now
    minutes = int(delta.total_seconds() // 60)
    hours = minutes // 60
    mins = minutes % 60

    text = (
        f"🔔 Следующая пара через *{hours} ч {mins} мин*\n\n"
        + format_pair(next_p, 1)
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ---------- Напоминания за 30 минут ----------

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Job, который проверяет, не пора ли напомнить о паре."""
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    group = job_data["group"]
    pair = job_data["pair"]

    # На всякий случай проверяем, что пользователь всё ещё в этой группе
    if user_group.get(chat_id) != group:
        return

    text = (
        f"⏰ *Напоминание:* через 30 минут начнётся пара!\n\n"
        + format_pair(pair, 1)
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


def schedule_reminders_for_today(application: Application, chat_id: int, group: str):
    """Ставит job'ы на сегодняшние пары для одного пользователя."""
    today_key = get_today_key()
    day_pairs = SCHEDULE.get(group, {}).get(today_key, [])
    if not day_pairs:
        return

    now = datetime.now()
    for pair in day_pairs:
        start_dt = parse_pair_start(pair)
        remind_dt = start_dt - timedelta(minutes=30)

        # Если время напоминания уже прошло — пропускаем
        if remind_dt <= now:
            continue

        # Удаляем старые job'ы с таким же именем, чтобы не дублировать
        job_name = f"remind_{chat_id}_{group}_{pair['start']}"
        for job in application.job_queue.get_jobs_by_name(job_name):
            job.schedule_removal()

        application.job_queue.run_once(
            send_reminder,
            when=remind_dt,
            data={"chat_id": chat_id, "group": group, "pair": pair},
            name=job_name,
        )


async def on_group_chosen_schedule_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """После выбора группы сразу ставим напоминания на сегодня."""
    query = update.callback_query
    chat_id = query.message.chat_id
    group = user_group.get(chat_id)
    if group:
        schedule_reminders_for_today(context.application, chat_id, group)


# ---------- Запуск ----------

def main():
    # Запускаем Flask-сервер, чтобы Render видел открытый порт
    from app import keep_alive
    keep_alive()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Установи переменную окружения BOT_TOKEN "
            "или временно вставь токен строкой в код."
        )

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("next", next_pair))
    application.add_handler(CommandHandler("tomorrow", tomorrow))
    application.add_handler(CallbackQueryHandler(choose_group, pattern="^group:"))
    # Дополнительный обработчик, который после выбора группы ставит напоминания
    application.add_handler(
        CallbackQueryHandler(on_group_chosen_schedule_reminders, pattern="^group:")
    )

    print("Бот запущен. Нажми Ctrl+C для остановки.")
    application.run_polling()


if __name__ == "__main__":
    main()