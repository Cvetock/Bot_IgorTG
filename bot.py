import os
from datetime import date, datetime, time
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
from calendar_utils import build_calendar

# ——————————————————————————————
#  Конфигурация и инициализация БД
# ——————————————————————————————
load_dotenv()
BOT_TOKEN    = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

engine       = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)

# ——————————————————————————————
#  Клавиатуры
# ——————————————————————————————
MAIN_MENU = ReplyKeyboardMarkup(
    [["📝 Запись", "📋 Моя запись"],
     ["❌ Отменить запись", "☎ Контакты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["📅 Мои слоты", "🗓 Указать дату и время"],
     ["📄 Все записи", "↩ Назад"]],
    resize_keyboard=True
)

# ——————————————————————————————
#  Состояния FSM
# ——————————————————————————————
SELECT_MASTER, SELECT_DATE, SELECT_TIME, ENTER_NAME, ENTER_PHONE = range(5)
MASTER_DATE, MASTER_ENTER_TIMES = range(5, 7)

# ID админа
ADMINS = {123456789}  # замените на свой Telegram ID

# ——————————————————————————————
#  Общие команды
# ——————————————————————————————
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    tg_id = update.effective_user.id

    # Регистрация клиента
    if not session.get(User, tg_id):
        session.add(User(tg_id=tg_id, role="client"))
        session.commit()

    # Проверка мастера
    master = session.query(Master).filter_by(tg_id=tg_id).first()
    session.close()

    if master:
        await update.message.reply_text(
            f"Привет, мастер {master.name}!", reply_markup=ADMIN_MENU
        )
    else:
        await update.message.reply_text(
            "Здравствуйте! Это бот онлайн-записи.", reply_markup=MAIN_MENU
        )

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш Telegram ID: {update.effective_user.id}")

async def add_master(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        return await update.message.reply_text("❌ У вас нет прав для этой команды.")
    args = context.args
    if len(args) != 2:
        return await update.message.reply_text("Использование: /addmaster <tg_id> <Имя>")

    tg_id, name = int(args[0]), args[1]
    session = SessionLocal()

    # Добавляем пользователя как мастера
    if not session.get(User, tg_id):
        session.add(User(tg_id=tg_id, role="master"))
    session.add(Master(tg_id=tg_id, name=name))

    try:
        session.commit()
        await update.message.reply_text(f"✅ Мастер {name} добавлен.")
    except Exception as e:
        session.rollback()
        await update.message.reply_text(f"❌ Ошибка: {e}")
    finally:
        session.close()

# ——————————————————————————————
#  Просмотр своих слотов (для мастера)
# ——————————————————————————————
async def view_availability(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    master = session.query(Master).filter_by(
        tg_id=update.effective_user.id
    ).first()

    avails = session.query(Availability)\
                    .filter_by(master_id=master.id)\
                    .order_by(Availability.date, Availability.time)\
                    .all()
    appts  = session.query(Appointment)\
                    .filter_by(master_id=master.id)\
                    .order_by(Appointment.date, Appointment.time)\
                    .all()
    session.close()

    dates = sorted({a.date for a in avails} | {b.date for b in appts})
    if not dates:
        return await update.message.reply_text(
            "Нет добавленных слотов или записей.", reply_markup=ADMIN_MENU
        )

    lines = []
    for d in dates:
        slots = [slot.time for slot in avails if slot.date == d]
        taken = {b.time for b in appts if b.date == d}
        slots_str = ", ".join(
            f"{t.strftime('%H:%M')} {'🔴' if t in taken else '🟢'}"
            for t in sorted(slots)
        )
        lines.append(f"{d}:\n{slots_str or '-'}")

    text = "\n\n".join(lines)
    await update.message.reply_text(text, reply_markup=ADMIN_MENU)

# ——————————————————————————————
#  Клиентская часть: бронирование
# ——————————————————————————————
async def book_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    masters = session.query(Master).all()
    session.close()

    kb = [[InlineKeyboardButton(m.name, callback_data=f"SEL_MASTER|{m.id}")]
          for m in masters]
    kb.append([InlineKeyboardButton("↩ Назад", callback_data="BACK_TO_MAIN")])

    await update.message.reply_text(
        "Выберите мастера:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return SELECT_MASTER

async def sel_master_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, master_id = query.data.split("|")
    context.user_data["master_id"] = int(master_id)

    session = SessionLocal()
    appts = session.query(Appointment).filter_by(master_id=int(master_id)).all()
    busy = {a.date for a in appts}
    session.close()

    today = date.today()
    cal = build_calendar(today.year, today.month, busy)
    await query.edit_message_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(cal))
    return SELECT_DATE

async def calendar_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Листание месяцев
    if data.startswith("CAL"):
        _, y, m = data.split("|")
        y, m = int(y), int(m)
        session = SessionLocal()
        appts = session.query(Appointment)\
                       .filter_by(master_id=context.user_data["master_id"]).all()
        busy = {a.date for a in appts}
        session.close()

        cal = build_calendar(y, m, busy)
        await query.edit_message_reply_markup(cal)
        return SELECT_DATE

    # Выбор даты
    if data.startswith("DAY"):
        _, iso = data.split("|")
        chosen = date.fromisoformat(iso)
        context.user_data["date"] = chosen

        session = SessionLocal()
        avails = session.query(Availability)\
                        .filter_by(master_id=context.user_data["master_id"], date=chosen)\
                        .all()
        appts = session.query(Appointment)\
                       .filter_by(master_id=context.user_data["master_id"], date=chosen)\
                       .all()
        session.close()

        taken = {a.time for a in appts}
        free_slots = [
            av.time.strftime("%H:%M")
            for av in avails if av.time not in taken
        ]

        kb = [
            [InlineKeyboardButton(ts, callback_data=f"TIME|{ts}")]
            for ts in free_slots
        ]
        kb.append([InlineKeyboardButton("↩ Назад", callback_data="BACK_TO_MAIN")])

        await query.edit_message_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))
        return SELECT_TIME

async def select_time_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, timestr = query.data.split("|")
    context.user_data["time"] = timestr
    await query.edit_message_text("Введите ваше имя:")
    return ENTER_NAME

async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_name"] = update.message.text
    await update.message.reply_text("Введите телефон:")
    return ENTER_PHONE

async def enter_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_phone"] = update.message.text

    session = SessionLocal()
    appt = Appointment(
        master_id   = context.user_data["master_id"],
        user_id     = update.effective_user.id,
        client_name = context.user_data["client_name"],
        client_phone= context.user_data["client_phone"],
        date        = context.user_data["date"],
        time        = datetime.strptime(context.user_data["time"], "%H:%M").time()
    )
    session.add(appt)
    session.commit()

    await update.message.reply_text("Ваша запись сохранена!", reply_markup=MAIN_MENU)

    master = session.query(Master).get(context.user_data["master_id"])
    await context.bot.send_message(
        chat_id=master.tg_id,
        text=(
            f"Новая запись:\n"
            f"{appt.client_name}, {appt.client_phone}\n"
            f"{appt.date} в {appt.time.strftime('%H:%M')}"
        )
    )
    session.close()
    context.user_data.clear()
    return ConversationHandler.END

# ——————————————————————————————
#  Просмотр и отмена записи
# ——————————————————————————————
async def my_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    appt = session.query(Appointment)\
                  .filter_by(user_id=update.effective_user.id)\
                  .order_by(Appointment.created_at.desc())\
                  .first()
    session.close()

    if not appt:
        return await update.message.reply_text("Записи нет.")
    await update.message.reply_text(
        f"{appt.date} в {appt.time.strftime('%H:%M')} — {appt.client_name}, {appt.client_phone}"
    )

async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    appt = session.query(Appointment)\
                  .filter_by(user_id=update.effective_user.id)\
                  .order_by(Appointment.created_at.desc())\
                  .first()
    session.close()

    if not appt:
        return await update.message.reply_text("Нечего отменять.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Подтвердить отмену", callback_data="DO_CANCEL")],
        [InlineKeyboardButton("↩ Назад", callback_data="BACK_TO_MAIN")]
    ])
    await update.message.reply_text(
        f"Текущая запись: {appt.date} в {appt.time.strftime('%H:%M')}",
        reply_markup=kb
    )

async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    session = SessionLocal()
    appt = session.query(Appointment)\
                  .filter_by(user_id=query.from_user.id)\
                  .order_by(Appointment.created_at.desc())\
                  .first()

    if appt:
        session.delete(appt)
        session.commit()
        await query.edit_message_text("Запись отменена.", reply_markup=MAIN_MENU)
    else:
        await query.answer("Нет записи.")
    session.close()

# ——————————————————————————————
#  FSM для мастера: добавление доступности
# ——————————————————————————————
async def master_avail_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    cal = build_calendar(today.year, today.month, busy=set())
    await update.message.reply_text(
        "Выберите дату для добавления слотов:", reply_markup=InlineKeyboardMarkup(cal)
    )
    return MASTER_DATE

async def master_date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, iso = query.data.split("|")
    chosen = date.fromisoformat(iso)
    context.user_data["avail_date"] = chosen

    await query.edit_message_text(
        f"Дата {chosen}. Введите слоты в формате 13.00,14.30:",
        reply_markup=None
    )
    return MASTER_ENTER_TIMES

async def master_enter_times(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slots = [t.replace(":", ".").strip() for t in update.message.text.split(",")]

    session = SessionLocal()
    me = session.query(Master).filter_by(tg_id=update.effective_user.id).first()
    for ts in slots:
        hh, mm = map(int, ts.split("."))
        session.add(Availability(
            master_id=me.id,
            date=context.user_data["avail_date"],
            time=time(hh, mm)
        ))
    session.commit()
    session.close()

    y, m = context.user_data["avail_date"].year, context.user_data["avail_date"].month
    cal = build_calendar(y, m, busy=set())
    await update.message.reply_text(
        f"Слоты сохранены для {context.user_data['avail_date']}.",
        reply_markup=InlineKeyboardMarkup(cal)
    )
    return MASTER_DATE

async def master_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Возвращаемся в меню мастера.", reply_markup=ADMIN_MENU)
    context.user_data.clear()
    return ConversationHandler.END

# ——————————————————————————————
#  Регистрация хендлеров и запуск
# ——————————————————————————————
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Общие команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("addmaster", add_master))
    app.add_handler(CommandHandler("mybooking", my_booking))
    app.add_handler(CommandHandler("cancelbooking", cancel_booking))

    # Просмотр слотов мастера
    app.add_handler(MessageHandler(filters.Regex("📅 Мои слоты"), view_availability))

    # FSM: бронирование клиента
    booking_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("📝 Запись"), book_start)],
        states={
            SELECT_MASTER: [CallbackQueryHandler(sel_master_cb, pattern="^SEL_MASTER")],
            SELECT_DATE:   [CallbackQueryHandler(calendar_cb, pattern="^(CAL|DAY|BACK_TO_MAIN)")],
            SELECT_TIME:   [CallbackQueryHandler(select_time_cb, pattern="^TIME\\|")],
            ENTER_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)],
            ENTER_PHONE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_phone)],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_cb, pattern="^DO_CANCEL"),
            MessageHandler(filters.Regex("^↩ Назад$"), lambda u, c: u.message.reply_text("Отмена.", reply_markup=MAIN_MENU))
        ],
        per_message=True
    )
    app.add_handler(booking_conv)

    # FSM: добавление слотов мастером
    master_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("🗓 Указать дату и время"), master_avail_start)],
        states={
            MASTER_DATE:        [CallbackQueryHandler(master_date_chosen, pattern="^DAY\\|")],
            MASTER_ENTER_TIMES: [MessageHandler(filters.Regex(r"^\d\d[.:]\d\d(,\s*\d\d[.:]\d\d)*$"), master_enter_times)],
        },
        fallbacks=[MessageHandler(filters.Regex("^↩ Назад$"), master_cancel)],
        per_message=True
    )
    app.add_handler(master_conv)

    # Кнопки отмены
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="^DO_CANCEL"))

    app.run_polling()

if __name__ == "__main__":
    main()
