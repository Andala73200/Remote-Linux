from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MACROS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}


def next_cron_time(schedule: str, now_epoch: int, timezone: str) -> str:
    raw = schedule.strip().lower()
    if raw == "@reboot":
        return "Au prochain démarrage"
    raw = MACROS.get(raw, raw)
    fields = raw.split()
    if len(fields) != 5:
        return "Non calculable"
    try:
        minute = _field(fields[0], 0, 59)
        hour = _field(fields[1], 0, 23)
        day = _field(fields[2], 1, 31)
        month = _field(fields[3], 1, 12)
        weekday = _field(fields[4], 0, 7, sunday=True)
    except ValueError:
        return "Non calculable"
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = datetime.timezone.utc
    current = datetime.datetime.fromtimestamp(now_epoch, zone)
    current = current.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    day_any, weekday_any = fields[2] == "*", fields[4] == "*"
    for _ in range(60 * 24 * 370):
        cron_weekday = (current.weekday() + 1) % 7
        day_match = current.day in day
        weekday_match = cron_weekday in weekday
        if not day_any and not weekday_any:
            date_match = day_match or weekday_match
        else:
            date_match = day_match and weekday_match
        if (
            current.minute in minute and current.hour in hour
            and current.month in month and date_match
        ):
            return current.strftime("%Y-%m-%d %H:%M")
        current += datetime.timedelta(minutes=1)
    return "Au-delà d’un an"


def _field(raw: str, minimum: int, maximum: int, sunday: bool = False) -> set[int]:
    result: set[int] = set()
    for group in raw.split(","):
        base, slash, step_text = group.partition("/")
        step = int(step_text) if slash else 1
        if step <= 0:
            raise ValueError
        if base == "*":
            start, stop = minimum, maximum
        elif "-" in base:
            start_text, stop_text = base.split("-", 1)
            start, stop = int(start_text), int(stop_text)
        else:
            start = stop = int(base)
        if start < minimum or stop > maximum or start > stop:
            raise ValueError
        result.update(range(start, stop + 1, step))
    if sunday and 7 in result:
        result.add(0)
        result.discard(7)
    return result
