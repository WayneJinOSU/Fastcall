import datetime
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


logger = logging.getLogger(__name__)

from robot_config_service.domain.errors import RobotConfigError


def _local_time(value):
    try:
        parsed = datetime.time.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise RobotConfigError("INVALID_CALLING_WINDOW", "Window time must be ISO HH:MM[:SS]") from error
    if parsed.tzinfo is not None or parsed.microsecond:
        raise RobotConfigError("INVALID_CALLING_WINDOW", "Window time cannot have offset or microseconds")
    return parsed


def validate_calling_windows(windows, excluded_dates=(), timezone="UTC"):
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, TypeError) as error:
        raise RobotConfigError("INVALID_TIMEZONE", "Unknown IANA timezone") from error
    if not isinstance(windows, list):
        raise RobotConfigError("INVALID_CALLING_WINDOW", "calling_windows must be a list")
    for window in windows:
        if not isinstance(window, dict) or set(window) != {"days", "start", "end"}:
            raise RobotConfigError("INVALID_CALLING_WINDOW", "Window requires days, start and end")
        days = window["days"]
        if (
            not isinstance(days, list) or not days
            or any(isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 7 for day in days)
            or len(set(days)) != len(days)
        ):
            raise RobotConfigError("INVALID_CALLING_WINDOW", "Window days must be unique ISO weekdays")
        start = _local_time(window["start"])
        end = _local_time(window["end"])
        if start == end:
            raise RobotConfigError("INVALID_CALLING_WINDOW", "Window start and end cannot be equal")
    for value in excluded_dates:
        try:
            datetime.date.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise RobotConfigError("INVALID_EXCLUDED_DATE", "Excluded dates must be ISO dates") from error


def _unambiguous(naive, zone):
    candidates = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        roundtrip = aware.astimezone(datetime.timezone.utc).astimezone(zone)
        if roundtrip.replace(tzinfo=None) == naive:
            candidates.append(aware)
    if len(candidates) != 2 or candidates[0].utcoffset() != candidates[1].utcoffset():
        return None
    return candidates[0]


def window_bounds(window, start_date, zone):
    start = _local_time(window["start"])
    end = _local_time(window["end"])
    start_naive = datetime.datetime.combine(start_date, start)
    end_date = start_date + datetime.timedelta(days=1) if end < start else start_date
    end_naive = datetime.datetime.combine(end_date, end)
    start_aware = _unambiguous(start_naive, zone)
    end_aware = _unambiguous(end_naive, zone)
    if start_aware is None or end_aware is None:
        logger.warning("Calling window disabled on DST boundary date=%s zone=%s", start_date, zone)
        return None
    return start_aware, end_aware


def is_within_calling_window(now, timezone, windows, excluded_dates=()):
    if not windows:
        return True
    zone = ZoneInfo(timezone)
    local_now = now.replace(tzinfo=datetime.timezone.utc).astimezone(zone)
    excluded = set(excluded_dates)
    for offset in (-1, 0):
        start_date = local_now.date() + datetime.timedelta(days=offset)
        if start_date.isoformat() in excluded:
            continue
        for window in windows:
            if start_date.isoweekday() not in window["days"]:
                continue
            bounds = window_bounds(window, start_date, zone)
            if bounds and bounds[0] <= local_now < bounds[1]:
                return True
    return False


def next_calling_window(now, timezone, windows, excluded_dates=()):
    if not windows:
        return now
    zone = ZoneInfo(timezone)
    local_now = now.replace(tzinfo=datetime.timezone.utc).astimezone(zone)
    excluded = set(excluded_dates)
    candidates = []
    for day_offset in range(0, 367):
        start_date = local_now.date() + datetime.timedelta(days=day_offset)
        if start_date.isoformat() in excluded:
            continue
        for window in windows:
            if start_date.isoweekday() not in window["days"]:
                continue
            bounds = window_bounds(window, start_date, zone)
            if not bounds:
                continue
            start, end = bounds
            if start <= local_now < end:
                return now
            if start > local_now:
                candidates.append(start)
    if not candidates:
        return None
    return min(candidates).astimezone(datetime.timezone.utc).replace(tzinfo=None)
