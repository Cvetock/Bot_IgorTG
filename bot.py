import os
from dotenv import load_dotenv
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram.ext import ConversationHandler
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, CallbackQueryHandler, filters
)
from models import Base, User, Master, Appointment
from calendar_utils import build_calendar
from telegram.ext import CallbackQueryHandler

SELECT_MASTER, SELECT_DATE, SELECT_TIME, ENTER_NAME, ENTER_PHONE = range(5)

# Загрузка конфигов
load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
DATABASE_URL  = os.getenv("DATABASE_URL")
WEBHOOK_HOST  = os.getenv("WEBHOOK_HOST")
WEBHOOK_PATH  = os.getenv("WEBHOOK_PATH")
WEBHOOK_PORT  = int(os.getenv("WEBHOOK_PORT", "8000"))

# Настройка SQLAlchemy
engine       = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)

# Главное меню клиента
MAIN_MENU = ReplyKeyboardMarkup([
    ["📝 Запись", "📋 Моя запись"],
    ["❌ Отменить запись", "☎ Контакты"]
], resize_keyboard=True)

# Админ-меню мастера
ADMIN_MENU = ReplyKeyboardMarkup([
    ["📄 Все записи", "↩ Назад"]
], resize_keyboard=True)

# -------- handlers --------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Регистрация пользователя и показ главного меню."""
    session = SessionLocal()
    tg_id = update.effective_user.id
    user = session.get(User, tg_id)
    if not user:
        user = User(tg_id=tg_id, role="client")
        session.add(user)
        session.commit()
    # Если мастер
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

# --- Запись: выбор мастера ---
async def book_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    masters = session.query(Master).all()
    session.close()

    kb = [[InlineKeyboardButton(m.name, callback_data=f"SEL_MASTER|{m.id}")]
          for m in masters]
    kb.append([InlineKeyboardButton("↩ Назад", callback_data="TO_MAIN")])
    await update.message.reply_text("Выберите мастера:", reply_markup=InlineKeyboardMarkup(kb))
    return SELECT_MASTER


async def sel_master_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, master_id = query.data.split("|")
    context.user_data["master_id"] = int(master_id)

    session = SessionLocal()
    appts = session.query(Appointment).filter_by(master_id=master_id).all()
    busy = {a.date for a in appts}
    session.close()

    today = date.today()
    cal = build_calendar(today.year, today.month, busy)
    await query.edit_message_text("Выберите дату:", reply_markup=cal)
    return SELECT_DATE


async def calendar_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("DAY"):
    _, iso = data.split("|")
    chosen = date.fromisoformat(iso)
    context.user_data["date"] = chosen

    # 1) Получаем все доступные слоты мастера на эту дату
    session = SessionLocal()
    master_id = context.user_data["master_id"]
    # слоты, которые мастер добавил
    avails = session.query(Availability) \
        .filter_by(master_id=master_id, date=chosen) \
        .all()
    # уже занятые (аппойнтменты)
    appts = session.query(Appointment) \
        .filter_by(master_id=master_id, date=chosen) \
        .all()
    session.close()

    taken = {a.time for a in appts}
    free_slots = [av.time.strftime("%H:%M") for av in avails if av.time not in taken]

    # 2) Строим клавиатуру с кнопками времени
    kb = [[InlineKeyboardButton(ts, callback_data=f"TIME|{ts}")]
          for ts in free_slots]
    kb.append([InlineKeyboardButton("↩ Назад", callback_data="BACK_TO_MASTERS")])
    await query.edit_message_text(
        "Выберите время:", reply_markup=InlineKeyboardMarkup(kb)
    )
    return SELECT_TIME

async def select_time_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, timestr = query.data.split("|")
    context.user_data["time"] = timestr
    await query.edit_message_text("Введите ваше имя:")
    return ENTER_NAME


async def back_to_masters(update, context):
    query = update.callback_query
    await query.answer()
    await book_start(update, context)



# --- Ввод имени и телефона ---
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
        date        = context.user_data["date"]
    )
    session.add(appt)
    session.commit()

    await update.message.reply_text("Ваша запись сохранена!", reply_markup=MAIN_MENU)
    master = session.query(Master).get(context.user_data["master_id"])
    await context.bot.send_message(
        master.tg_id,
        f"Новая запись:\n{appt.client_name}, {appt.client_phone}\nДата: {appt.date}"
    )
    session.close()
    context.user_data.clear()
    return ConversationHandler.END


# --- /mybooking и /cancelbooking ---
async def my_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    appt = session.query(Appointment)\
        .filter_by(user_id=update.effective_user.id)\
        .order_by(Appointment.created_at.desc())\
        .first()
    session.close()

    if not appt:
        text = "Записи нет."
    else:
        text = f"{appt.date} — {appt.client_name}, {appt.client_phone}"
    await update.message.reply_text(text)

async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    appt = session.query(Appointment)\
        .filter_by(user_id=update.effective_user.id)\
        .order_by(Appointment.created_at.desc())\
        .first()

    if not appt:
        await update.message.reply_text("Нечего отменять.")
    else:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Подтвердить отмену", callback_data="DO_CANCEL")],
             [InlineKeyboardButton("↩ Отмена", callback_data="TO_MAIN")]]
        )
        await update.message.reply_text(
            f"Текущая запись: {appt.date} — {appt.client_name}",
            reply_markup=kb
        )
    session.close()

async def cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    session = SessionLocal()
    appt = session.query(Appointment)\
        .filter_by(user_id=query.from_user.id)\
        .order_by(Appointment.created_at.desc())\
        .first()
    if appt:
        session.delete(appt)
        session.commit()
        await query.edit_message_text("Запись отменена.", reply_markup=None)
    else:
        await query.answer("Нет записи.")
    session.close()

# --- Контакты ---
async def contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("📞 +7 123 456-78-90\n"
            "📱 @your_instagram\n"
            "🌐 your_site.com")
    await update.message.reply_text(text)

# --- Админ-меню мастера ---
ADMINS = {834598783}
async def add_master(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ У вас нет прав для добавления мастеров.")
        return

    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Использование: /addmaster <tg_id> <имя>")
        return

    tg_id, name = args
    tg_id = int(tg_id)

    session = SessionLocal()

    # Добавляем пользователя, если его нет
    existing_user = session.query(User).get(tg_id)
    if not existing_user:
        session.add(User(tg_id=tg_id, role="master"))

    # Добавляем мастера
    master = Master(tg_id=tg_id, name=name)
    session.add(master)

    try:
        session.commit()
        await update.message.reply_text(f"✅ Мастер {name} добавлен.")
    except Exception as e:
        session.rollback()
        await update.message.reply_text(f"❌ Ошибка при добавлении мастера: {e}")
    finally:
        session.close()

async def admin_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    master = session.query(Master).filter_by(tg_id=update.effective_user.id).first()
    appts = master.appointments if master else []
    session.close()

    if not appts:
        await update.message.reply_text("Нет клиентов.")
    else:
        lines = [f"{a.date}: {a.client_name}, {a.client_phone}" for a in appts]
        await update.message.reply_text("\n".join(lines))

# -------- запуск приложения --------

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mybooking", my_booking))
    app.add_handler(CommandHandler("cancelbooking", cancel_booking))
    app.add_handler(CommandHandler("contacts", contacts))

    # Рабочие хендлеры
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("📝 Запись"), book_start)],
        states={
            SELECT_MASTER: [CallbackQueryHandler(sel_master_cb, pattern="SEL_MASTER")],
            SELECT_DATE: [CallbackQueryHandler(calendar_cb, pattern="^(CAL|DAY|BACK_TO_MASTERS)")],
            SELECT_TIME: [CallbackQueryHandler(select_time_cb, pattern="^TIME\\|")],
            ENTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name)],
            ENTER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_phone)],
        },
        fallbacks=[],
        per_message=True
    )
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="DO_CANCEL"))
    app.add_handler(MessageHandler(filters.Regex("📋 Моя запись"), my_booking))
    app.add_handler(MessageHandler(filters.Regex("❌ Отменить запись"), cancel_booking))
    app.add_handler(MessageHandler(filters.Regex("☎ Контакты"), contacts))
    app.add_handler(CommandHandler("addmaster", add_master))

    # Админ-мастер
    app.add_handler(MessageHandler(filters.Regex("📄 Все записи"), admin_all))
    app.add_handler(CallbackQueryHandler(back_to_masters, pattern="^BACK_TO_MASTERS$"))
    # Настройка Webhook
    app.run_polling()

if __name__ == "__main__":
    main()
