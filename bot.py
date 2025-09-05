import os
import logging
from datetime import date, datetime, time, timedelta

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)

from models import Base, User, Master, Appointment, Availability
from calendar_utils import build_calendar  # календарь бронирования: CAL|, DAY|, BACK/IGNORE

# ——— ЛОГИРОВАНИЕ ——————————————————————————————————
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)

# ——— БАЗА ——————————————————————————————————
load_dotenv()
BOT_TOKEN    = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

engine       = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)

# ——— МЕНЮ ——————————————————————————————————
MAIN_MENU = ReplyKeyboardMarkup(
    [["📝 Запись", "📋 Моя запись"],
     ["❌ Отменить запись", "☎ Контакты"]],
    resize_keyboard=True
)
ADMIN_MENU = ReplyKeyboardMarkup(
    [["📅 Мои слоты", "🗓 Указать дату и время"],
     ["📄 Все записи", "🗑 Удалить запись"],
     ["↩ Назад"]],
    resize_keyboard=True
)

# ——— FSM ——————————————————————————————————
SELECT_MASTER, SELECT_DATE, SELECT_TIME, ENTER_NAME, ENTER_PHONE = range(5)
MASTER_DATE, MASTER_ENTER_TIMES = range(5, 7)
DEL_DATE, DEL_TIME = range(7, 9)

ADMINS = {123456789}  # ваш Telegram ID

# ——— УТИЛИТА: календарь удаления на базе build_calendar ————————————————
def build_delete_calendar(year: int, month: int, busy_dates: set[date]) -> InlineKeyboardMarkup:
    # Используем тот же протокол, что в бронировании (CAL|, DAY|).
    # Но кликабельными делаем только busy_dates, остальные — IGNORE.
    prev_month = month - 1 or 12
    prev_year  = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year  = year + 1 if month == 12 else year

    header = [
        InlineKeyboardButton("◀", callback_data=f"CAL|{prev_year}|{prev_month}"),
        InlineKeyboardButton(f"{month}/{year}", callback_data="IGNORE"),
        InlineKeyboardButton("▶", callback_data=f"CAL|{next_year}|{next_month}")
    ]
    week_days = ["Mo","Tu","We","Th","Fr","Sa","Su"]
    rows = [[InlineKeyboardButton(w, callback_data="IGNORE") for w in week_days]]

    first = date(year, month, 1)
    shift = first.weekday()
    row = [InlineKeyboardButton(" ", callback_data="IGNORE")] * shift

    d = first
    while d.month == month:
        if d in busy_dates:
            row.append(InlineKeyboardButton(str(d.day), callback_data=f"DAY|{d.isoformat()}"))
        else:
            row.append(InlineKeyboardButton(str(d.day), callback_data="IGNORE"))
        if len(row) == 7:
            rows.append(row)
            row = []
        d += timedelta(days=1)

    if row:
        row += [InlineKeyboardButton(" ", callback_data="IGNORE")] * (7 - len(row))
        rows.append(row)

    rows.append([InlineKeyboardButton("↩ Назад", callback_data="BACK")])
    return InlineKeyboardMarkup([header] + rows)

# ——— ОБЩИЕ ——————————————————————————————————
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    tg_id = update.effective_user.id
    if not session.get(User, tg_id):
        session.add(User(tg_id=tg_id, role="client"))
        session.commit()
    master = session.query(Master).filter_by(tg_id=tg_id).first()
    session.close()
    if master:
        await update.message.reply_text(f"Привет, мастер {master.name}!", reply_markup=ADMIN_MENU)
    else:
        await update.message.reply_text("Здравствуйте! Это бот онлайн-записи.", reply_markup=MAIN_MENU)

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш Telegram ID: {update.effective_user.id}")

async def add_master(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ У вас нет прав.")
    if len(context.args) != 2:
        return await update.message.reply_text("Использование: /addmaster <tg_id> <Имя>")
    tg_id, name = int(context.args[0]), context.args[1]
    session = SessionLocal()
    if not session.get(User, tg_id):
        session.add(User(tg_id=tg_id, role="master"))
    session.add(Master(tg_id=tg_id, name=name))
    session.commit()
    session.close()
    await update.message.reply_text(f"✅ Мастер {name} добавлен.")

async def ignore_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

# ——— КЛИЕНТ: МОЯ/ОТМЕНА/КОНТАКТЫ ————————————————————————————
async def my_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    appt = session.query(Appointment).filter_by(user_id=update.effective_user.id)\
            .order_by(Appointment.created_at.desc()).first()
    session.close()
    if not appt or appt.time is None:
        return await update.message.reply_text("Записи нет.")
    await update.message.reply_text(f"{appt.date} в {appt.time.strftime('%H:%M')} — {appt.client_name}, {appt.client_phone}")

async def send_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📞 +7 123 456-78-90\n🌐 your_site.com")

async def cancel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    appt = session.query(Appointment).filter_by(user_id=update.effective_user.id)\
            .order_by(Appointment.created_at.desc()).first()
    session.close()
    if not appt or appt.time is None:
        return await update.message.reply_text("Нечего отменять.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Подтвердить отмену", callback_data="CANCEL_YES")],
        [InlineKeyboardButton("↩ Назад", callback_data="BACK")]
    ])
    await update.message.reply_text(f"Текущая запись: {appt.date} в {appt.time.strftime('%H:%M')}", reply_markup=kb)

async def cancel_yes_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session = SessionLocal()
    appt = session.query(Appointment).filter_by(user_id=query.from_user.id)\
            .order_by(Appointment.created_at.desc()).first()
    if appt:
        session.delete(appt)
        session.commit()
        await query.edit_message_text("Запись отменена.")
    session.close()

# ——— КЛИЕНТ: БРОНИРОВАНИЕ (FSM) ————————————————————————————
async def book_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    masters = session.query(Master).all()
    session.close()
    kb = [[InlineKeyboardButton(m.name, callback_data=f"SEL_MASTER|{m.id}")] for m in masters]
    kb.append([InlineKeyboardButton("↩ Назад", callback_data="BACK")])
    await update.message.reply_text("Выберите мастера:", reply_markup=InlineKeyboardMarkup(kb))
    return SELECT_MASTER

async def sel_master_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, mid = query.data.split("|")
    context.user_data["master_id"] = int(mid)
    today = date.today()
    cal = build_calendar(today.year, today.month, busy=set())
    await query.edit_message_text("Выберите дату:", reply_markup=cal)
    return SELECT_DATE

async def calendar_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("CAL|"):
        _, y, m = data.split("|")
        cal = build_calendar(int(y), int(m), busy=set())
        await query.edit_message_reply_markup(reply_markup=cal)
        return SELECT_DATE
    if data == "BACK":
        await query.message.reply_text("Главное меню.", reply_markup=MAIN_MENU)
        await query.delete_message()
        return ConversationHandler.END
    if data.startswith("DAY|"):
        _, iso = data.split("|")
        chosen = date.fromisoformat(iso)
        context.user_data["date"] = chosen
        session = SessionLocal()
        avails = session.query(Availability).filter_by(master_id=context.user_data["master_id"], date=chosen).all()
        appts = session.query(Appointment).filter_by(master_id=context.user_data["master_id"], date=chosen).all()
        session.close()
        taken = {a.time for a in appts if a.time is not None}
        free = [av.time.strftime("%H:%M") for av in avails if av.time not in taken]
        kb = [[InlineKeyboardButton(ts, callback_data=f"TIME|{ts}")] for ts in free]
        kb.append([InlineKeyboardButton("↩ Назад", callback_data="BACK")])
        await query.edit_message_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))
        return SELECT_TIME

async def select_time_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, ts = query.data.split("|")
    context.user_data["time"] = ts
    await query.edit_message_text("Введите ваше имя:")
    return ENTER_NAME

async def enter_name_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_name"] = update.message.text
    await update.message.reply_text("Введите телефон:")
    return ENTER_PHONE

async def enter_phone_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_phone"] = update.message.text
    session = SessionLocal()
    appt = Appointment(
        master_id=context.user_data["master_id"],
        user_id=update.effective_user.id,
        client_name=context.user_data["client_name"],
        client_phone=context.user_data["client_phone"],
        date=context.user_data["date"],
        time=datetime.strptime(context.user_data["time"], "%H:%M").time(),
    )
    session.add(appt)
    session.commit()
    master = session.query(Master).get(context.user_data["master_id"])
    try:
        await context.bot.send_message(chat_id=master.tg_id,
            text=f"Новая запись:\n{appt.client_name}, {appt.client_phone}\n{appt.date} в {appt.time.strftime('%H:%M')}")
    except Exception:
        pass
    session.close()
    await update.message.reply_text("Ваша запись сохранена!", reply_markup=MAIN_MENU)
    context.user_data.clear()
    return ConversationHandler.END

# ——— МАСТЕР: МОИ СЛОТЫ / ВСЕ ЗАПИСИ ————————————————————————————
async def view_availability(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    master = session.query(Master).filter_by(tg_id=update.effective_user.id).first()
    avails = session.query(Availability).filter_by(master_id=master.id).order_by(Availability.date, Availability.time).all()
    appts  = session.query(Appointment ).filter_by(master_id=master.id).order_by(Appointment.date, Appointment.time).all()
    session.close()
    dates = sorted({a.date for a in avails} | {b.date for b in appts})
    if not dates:
        return await update.message.reply_text("Нет слотов или записей.", reply_markup=ADMIN_MENU)
    lines = []
    for d in dates:
        slots = sorted(slot.time for slot in avails if slot.date == d)
        taken = {b.time for b in appts if b.date == d and b.time is not None}
        msg = ", ".join(f"{t.strftime('%H:%M')} {'🔴' if t in taken else '🟢'}" for t in slots) or "-"
        lines.append(f"{d}:\n{msg}")
    await update.message.reply_text("\n\n".join(lines), reply_markup=ADMIN_MENU)

async def admin_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    master = session.query(Master).filter_by(tg_id=update.effective_user.id).first()
    appts  = session.query(Appointment ).filter_by(master_id=master.id).order_by(Appointment.date, Appointment.time).all()
    session.close()
    appts = [a for a in appts if a.time is not None]
    if not appts:
        return await update.message.reply_text("Никто ещё не записался.", reply_markup=ADMIN_MENU)
    lines = [f"{a.date} в {a.time.strftime('%H:%M')} — {a.client_name}, {a.client_phone}" for a in appts]
    await update.message.reply_text("\n".join(lines), reply_markup=ADMIN_MENU)

# ——— МАСТЕР: УДАЛЕНИЕ ЗАПИСИ (FSM как в бронировании) ——————————————————
# --- Календарь для удаления ---
def build_delete_calendar(year: int, month: int, busy_dates: set[date]) -> InlineKeyboardMarkup:
    prev_month = month - 1 or 12
    prev_year  = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year  = year + 1 if month == 12 else year

    header = [
        InlineKeyboardButton("◀", callback_data=f"DEL_CAL|{prev_year}|{prev_month}"),
        InlineKeyboardButton(f"{month}/{year}", callback_data="IGNORE"),
        InlineKeyboardButton("▶", callback_data=f"DEL_CAL|{next_year}|{next_month}")
    ]
    week_days = ["Mo","Tu","We","Th","Fr","Sa","Su"]
    rows = [[InlineKeyboardButton(w, callback_data="IGNORE") for w in week_days]]

    first = date(year, month, 1)
    shift = first.weekday()
    row = [InlineKeyboardButton(" ", callback_data="IGNORE")] * shift

    d = first
    while d.month == month:
        if d in busy_dates:
            row.append(InlineKeyboardButton(str(d.day), callback_data=f"DEL_DAY|{d.isoformat()}"))
        else:
            row.append(InlineKeyboardButton(str(d.day), callback_data="IGNORE"))
        if len(row) == 7:
            rows.append(row)
            row = []
        d += timedelta(days=1)

    if row:
        row += [InlineKeyboardButton(" ", callback_data="IGNORE")] * (7 - len(row))
        rows.append(row)

    rows.append([InlineKeyboardButton("↩ Назад", callback_data="DEL_BACK")])
    return InlineKeyboardMarkup([header] + rows)


# --- Старт удаления ---
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    master = session.query(Master).filter_by(tg_id=update.effective_user.id).first()
    context.user_data["master_id"] = master.id
    busy = {a.date for a in session.query(Appointment).filter_by(master_id=master.id).all()}
    session.close()

    if not busy:
        return await update.message.reply_text("Нет записей для удаления.", reply_markup=ADMIN_MENU)

    today = date.today()
    cal = build_delete_calendar(today.year, today.month, busy)
    await update.message.reply_text("Выберите дату для удаления:", reply_markup=cal)
    return DEL_DATE


# --- Обработка кликов по календарю удаления ---
async def delete_date_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Листание месяцев
    if data.startswith("DEL_CAL|"):
        _, y, m = data.split("|")
        y, m = int(y), int(m)
        session = SessionLocal()
        busy = {a.date for a in session.query(Appointment)
                .filter_by(master_id=context.user_data["master_id"]).all()}
        session.close()
        cal = build_delete_calendar(y, m, busy)
        await query.edit_message_reply_markup(reply_markup=cal)
        return DEL_DATE

    # Назад
    if data == "DEL_BACK":
        await query.message.reply_text("Возвращаемся в меню мастера.", reply_markup=ADMIN_MENU)
        await query.delete_message()
        context.user_data.clear()
        return ConversationHandler.END

    # Выбор даты
    if data.startswith("DEL_DAY|"):
        _, iso = data.split("|")
        chosen = date.fromisoformat(iso)

        session = SessionLocal()
        appts = session.query(Appointment).filter_by(
            master_id=context.user_data["master_id"], date=chosen
        ).order_by(Appointment.time).all()
        session.close()

        appts = [a for a in appts if a.time is not None]
        if not appts:
            await query.edit_message_text(f"Нет записей на {chosen}.", reply_markup=None)
            return ConversationHandler.END

        kb = [[InlineKeyboardButton(a.time.strftime("%H:%M"), callback_data=f"DEL_APPT|{a.id}")]
              for a in appts]
        kb.append([InlineKeyboardButton("↩ Назад", callback_data="DEL_BACK")])
        await query.edit_message_text("Выберите время для удаления:", reply_markup=InlineKeyboardMarkup(kb))
        return DEL_TIME


# --- Удаление конкретной записи ---
async def delete_time_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "DEL_BACK":
        await query.message.reply_text("Возвращаемся в меню мастера.", reply_markup=ADMIN_MENU)
        await query.delete_message()
        context.user_data.clear()
        return ConversationHandler.END

    if data.startswith("DEL_APPT|"):
        _, appt_id = data.split("|")
        session = SessionLocal()
        appt = session.get(Appointment, int(appt_id))
        if not appt:
            session.close()
            await query.edit_message_text("Запись не найдена.", reply_markup=None)
            return ConversationHandler.END

        # Уведомляем клиента
        if appt.user_id:
            try:
                await context.bot.send_message(
                    chat_id=appt.user_id,
                    text=f"Ваша запись {appt.date} в {appt.time.strftime('%H:%M')} отменена мастером."
                )
            except Exception:
                pass

        session.delete(appt)
        session.commit()
        session.close()

        await query.edit_message_text("Запись удалена.", reply_markup=None)
        context.user_data.clear()
        return ConversationHandler.END

# ——— МАСТЕР: ДОБАВЛЕНИЕ СЛОТОВ (FSM) ————————————————————————————
async def master_avail_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    cal = build_calendar(today.year, today.month, busy=set())
    await update.message.reply_text("Выберите дату для добавления слотов:", reply_markup=cal)
    return MASTER_DATE

async def master_date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("CAL|"):
        _, y, m = data.split("|")
        cal = build_calendar(int(y), int(m), busy=set())
        await query.edit_message_reply_markup(reply_markup=cal)
        return MASTER_DATE
    if data == "BACK":
        await query.message.reply_text("Меню мастера.", reply_markup=ADMIN_MENU)
        await query.delete_message()
        return ConversationHandler.END
    if data.startswith("DAY|"):
        _, iso = data.split("|")
        chosen = date.fromisoformat(iso)
        context.user_data["avail_date"] = chosen
        await query.edit_message_text(f"Введите слоты для {chosen} (например, 13.00,14.30):")
        return MASTER_ENTER_TIMES

async def master_enter_times(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slots = [t.strip().replace(":", ".") for t in update.message.text.split(",")]
    session = SessionLocal()
    me = session.query(Master).filter_by(tg_id=update.effective_user.id).first()
    for ts in slots:
        hh, mm = map(int, ts.split("."))
        session.add(Availability(master_id=me.id, date=context.user_data["avail_date"], time=time(hh, mm)))
    session.commit()
    session.close()
    y, m = context.user_data["avail_date"].year, context.user_data["avail_date"].month
    cal = build_calendar(y, m, busy=set())
    await update.message.reply_text(f"Слоты добавлены для {context.user_data['avail_date']}.", reply_markup=cal)
    return MASTER_DATE

# ——— РЕГИСТРАЦИЯ ——————————————————————————————————
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("addmaster", add_master))

    # Клиентские кнопки (всегда активны)
    app.add_handler(MessageHandler(filters.Regex("^📝 Запись$"), book_start))
    app.add_handler(MessageHandler(filters.Regex("^📋 Моя запись$"), my_booking))
    app.add_handler(MessageHandler(filters.Regex("^❌ Отменить запись$"), cancel_start))
    app.add_handler(CallbackQueryHandler(cancel_yes_cb, pattern="^CANCEL_YES$"))
    app.add_handler(MessageHandler(filters.Regex("^☎ Контакты$"), send_contacts))

    # Мастерские кнопки (всегда активны)
    app.add_handler(MessageHandler(filters.Regex("^📅 Мои слоты$"), view_availability))
    app.add_handler(MessageHandler(filters.Regex("^📄 Все записи$"), admin_all))

    # FSM удаления записи (календарь как в бронировании: CAL| и DAY|)
    delete_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑 Удалить запись$"), delete_start)],
        states={
            DEL_DATE: [
                CallbackQueryHandler(delete_date_cb, pattern=r"^DEL_CAL\|"),
                CallbackQueryHandler(delete_date_cb, pattern=r"^DEL_DAY\|"),
                CallbackQueryHandler(delete_date_cb, pattern=r"^DEL_BACK$"),
                CallbackQueryHandler(ignore_cb, pattern=r"^IGNORE$")
            ],
            DEL_TIME: [
                CallbackQueryHandler(delete_time_cb, pattern=r"^DEL_APPT\|"),
                CallbackQueryHandler(delete_time_cb, pattern=r"^DEL_BACK$")
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^↩ Назад$"), start)],
        per_user=True
    )
    app.add_handler(delete_conv)

    # FSM бронирования
    booking_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📝 Запись$"), book_start)],
        states={
            SELECT_MASTER: [
                CallbackQueryHandler(sel_master_cb, pattern=r"^SEL_MASTER\|"),
                CallbackQueryHandler(ignore_cb,     pattern=r"^IGNORE$")
            ],
            SELECT_DATE: [
                CallbackQueryHandler(calendar_cb, pattern=r"^CAL\|"),
                CallbackQueryHandler(calendar_cb, pattern=r"^DAY\|"),
                CallbackQueryHandler(calendar_cb, pattern=r"^BACK$"),
                CallbackQueryHandler(ignore_cb,   pattern=r"^IGNORE$")
            ],
            SELECT_TIME: [
                CallbackQueryHandler(select_time_cb, pattern=r"^TIME\|"),
                CallbackQueryHandler(ignore_cb,      pattern=r"^IGNORE$")
            ],
            ENTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name_cb)],
            ENTER_PHONE:[MessageHandler(filters.TEXT & ~filters.COMMAND, enter_phone_cb)]
        },
        fallbacks=[MessageHandler(filters.Regex("^↩ Назад$"), start)],
        per_user=True
    )
    app.add_handler(booking_conv)

    # FSM добавления слотов мастером (тот же календарь)
    avail_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗓 Указать дату и время$"), master_avail_start)],
        states={
            MASTER_DATE: [
                CallbackQueryHandler(master_date_chosen, pattern=r"^CAL\|"),
                CallbackQueryHandler(master_date_chosen, pattern=r"^DAY\|"),
                CallbackQueryHandler(master_date_chosen, pattern=r"^BACK$"),
                CallbackQueryHandler(ignore_cb,          pattern=r"^IGNORE$")
            ],
            MASTER_ENTER_TIMES: [
                MessageHandler(filters.Regex(r"^\d{1,2}[.:]\d{2}(,\s*\d{1,2}[.:]\d{2})*$"), master_enter_times)
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^↩ Назад$"), start)],
        per_user=True
    )
    app.add_handler(avail_conv)

    # IGNORE глобально
    app.add_handler(CallbackQueryHandler(ignore_cb, pattern=r"^IGNORE$"))

    app.run_polling()

if __name__ == "__main__":
    main()
