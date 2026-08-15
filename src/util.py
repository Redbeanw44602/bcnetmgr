from datetime import datetime, date as date_, timedelta, timezone


def consume_front(ls: list):
    if len(ls) == 0:
        return None
    return ls.pop(0)


def emoji_from_country(code: str):
    match code.upper():
        case 'US':
            return '🇺🇸'
        case 'HK':
            return '🇭🇰'
        case 'TW':
            return '🇹🇼'
        case _:
            return ''


def b2mb(bbytes: int) -> float:
    return bbytes / 1024 / 1024


def s2dhms(s: int) -> tuple[int, int, int, int]:
    d = s // 86400  # 60 * 60 * 24
    s %= 86400

    h = s // 3600  # 60 * 60
    s %= 3600

    m = s // 60
    s = s % 60

    return d, h, m, s


def get_date_now() -> date_:  # UTC+8
    # FIXME: ZoneInfo is not usable:
    # https://github.com/cloudflare/workerd/issues/1972
    utc8 = timezone(timedelta(hours=8))
    return datetime.now(utc8).date()


def date2str(date: date_) -> str:
    return date.strftime('%Y-%m-%d')


def str2date(date: str) -> date_:
    return datetime.strptime(date, '%Y-%m-%d').date()


def get_weekdays(day_in_week: date_) -> list[date_]:
    start_day = day_in_week - timedelta(days=day_in_week.weekday())
    return [start_day + timedelta(days=x) for x in range(7)]


def get_monthdays(day_in_month: date_) -> list[date_]:
    ret = []
    day_in_month = day_in_month.replace(day=1)
    month = day_in_month.month

    while day_in_month.month == month:
        ret.append(day_in_month)
        day_in_month += timedelta(days=1)

    return ret


def to_integer(s: str) -> int | None:
    try:
        return int(s)
    except Exception:
        pass


def unchecked_expect(*args):
    pass
