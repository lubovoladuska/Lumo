"""Форматирование дат для ответов навыков."""

from datetime import datetime

MONTHS = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def human_datetime(dt: datetime, now: datetime) -> str:
    """«сегодня в 18:00», «завтра в 09:00», «5 ноября в 10:30»."""
    days = (dt.date() - now.date()).days
    if days == 0:
        day = "сегодня"
    elif days == 1:
        day = "завтра"
    elif days == 2:
        day = "послезавтра"
    else:
        day = f"{dt.day} {MONTHS[dt.month - 1]}"
    time = dt.strftime("%H:%M:%S") if dt.second and days == 0 else dt.strftime("%H:%M")
    return f"{day} в {time}"
