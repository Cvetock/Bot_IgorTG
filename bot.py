import os
from dotenv import load_dotenv
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
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

async def sel_master_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, master_id = query.data.split("|")
    context.user_data["master_id"] = int(master_id)

    # Соберём занятые даты этого мастера
    session = SessionLocal()
    appts = session.query(Appointment).filter_by(master_id=master_id).all()
    busy = {a.date for a in appts}
    session.close()

    today = date.today()
    cal = build_calendar(today.year, today.month, busy)
    await query.edit_message_text("Выберите дату:", reply_markup=cal)

async def calendar_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Листание месяцев и выбор дня."""
    query = update.callback_query
    data = query.data

    if data.startswith("CAL"):
        _, y, m = data.split("|")
        y, m = int(y), int(m)
        session = SessionLocal()
        master_id = context.user_data["master_id"]
        appts = session.query(Appointment).filter_by(master_id=master_id).all()
        busy = {a.date for a in appts}
        session.close()

        cal = build_calendar(y, m, busy)
        await query.edit_message_reply_markup(cal)

    elif data.startswith("DAY"):
        _, iso = data.split("|")
        chosen = date.fromisoformat(iso)
        context.user_data["date"] = chosen
        await query.edit_message_text("Введите ваше имя:")
        return

    elif data == "BACK_TO_MASTERS":
        await query.edit_message_text("Выберите мастера:", reply_markup=None)
        await book_start(update, context)

async def back_to_masters(update, context):
    query = update.callback_query
    await query.answer()
    await book_start(update, context)



# --- Ввод имени и телефона ---
async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_name"] = update.message.text
    await update.message.reply_text("Введите телефон:")
async def enter_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_phone"] = update.message.text

    # Сохраняем запись
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

    # Уведомляем клиента и мастера
    await update.message.reply_text("Ваша запись сохранена!", reply_markup=MAIN_MENU)
    master = session.query(Master).get(context.user_data["master_id"])
    await context.bot.send_message(
        master.tg_id,
        f"Новая запись:\n{appt.client_name}, {appt.client_phone}\n"
        f"Дата: {appt.date}"
    )
    session.close()

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
    app.add_handler(MessageHandler(filters.Regex("📝 Запись"), book_start))
    app.add_handler(CallbackQueryHandler(sel_master_cb, pattern="SEL_MASTER"))
    app.add_handler(CallbackQueryHandler(calendar_cb, pattern="^(CAL|DAY|BACK_TO_MASTERS)"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name), 1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, enter_phone), 2)
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="DO_CANCEL"))
    app.add_handler(MessageHandler(filters.Regex("📋 Моя запись"), my_booking))
    app.add_handler(MessageHandler(filters.Regex("❌ Отменить запись"), cancel_booking))
    app.add_handler(MessageHandler(filters.Regex("☎ Контакты"), contacts))

    # Админ-мастер
    app.add_handler(MessageHandler(filters.Regex("📄 Все записи"), admin_all))
    app.add_handler(CallbackQueryHandler(back_to_masters, pattern="^BACK_TO_MASTERS$"))
    # Настройка Webhook
    app.bot.set_webhook(url=f"{WEBHOOK_HOST}{WEBHOOK_PATH}")
    app.run_webhook(
        listen="0.0.0.0",
        port=WEBHOOK_PORT,
        webhook_path=WEBHOOK_PATH
    )

if __name__ == "__main__":
    main()
