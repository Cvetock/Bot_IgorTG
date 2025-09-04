from datetime import date, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def build_calendar(year: int, month: int, busy: set[date]) -> InlineKeyboardMarkup:
    # Навигационная шапка
    prev_month = month - 1 or 12
    prev_year  = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year  = year + 1 if month == 12 else year

    header = [
        InlineKeyboardButton("◀", callback_data=f"CAL|{prev_year}|{prev_month}"),
        InlineKeyboardButton(f"{month}/{year}", callback_data="IGNORE"),
        InlineKeyboardButton("▶", callback_data=f"CAL|{next_year}|{next_month}")
    ]

    # Дни недели
    week_days = ["Mo","Tu","We","Th","Fr","Sa","Su"]
    rows = [[InlineKeyboardButton(w, callback_data="IGNORE") for w in week_days]]

    # Заполнение чисел
    first = date(year, month, 1)
    start_weekday = first.weekday()  # 0=Mon … 6=Sun
    row = [InlineKeyboardButton(" ", callback_data="IGNORE")] * start_weekday
    d = first

    while d.month == month:
        txt = str(d.day)
        if d in busy:
            txt = f"~{txt}~"
            data = "IGNORE"
        else:
            data = f"DAY|{d.isoformat()}"
        row.append(InlineKeyboardButton(txt, callback_data=data))

        if len(row) == 7:
            rows.append(row)
            row = []
        d += timedelta(days=1)

    # Заполнение последней строки
    if row:
        row += [InlineKeyboardButton(" ", callback_data="IGNORE")] * (7 - len(row))
        rows.append(row)

    # Кнопка «Назад»
    rows.append([InlineKeyboardButton("↩ Назад", callback_data="BACK_TO_MASTERS")])

    return InlineKeyboardMarkup([header] + rows)
