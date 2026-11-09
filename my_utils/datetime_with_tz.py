"""
带时区的 datetime
"""
import time
from datetime import datetime, tzinfo
from pytz import timezone


class DatetimeWithTZ(datetime):
    country_tz = None

    @classmethod
    def fromtimestamp(cls, ts: float, tz: tzinfo = None) -> datetime:
        if tz is None:
            tz = cls.country_tz
        return super().fromtimestamp(ts, tz)

    @classmethod
    def strptime(cls, date_string: str, format_: str, tz: tzinfo = None) -> datetime:
        if tz is None:
            tz = cls.country_tz
        return tz.localize(datetime.strptime(date_string, format_))

    @classmethod
    def now(cls, tz: tzinfo = None) -> datetime:
        if tz is None:
            tz = cls.country_tz
        return datetime.now(tz)

country_tz_mapping = {
    "chile": timezone('America/Santiago'),
    "colombia": timezone('America/Bogota'),
    "mexico": timezone('America/Mexico_City'),
    "pakistan": timezone('Asia/Karachi'),
    "peru": timezone('America/Lima'),
    "philippines": timezone('Asia/Manila'),
    "vietnam": timezone('Asia/Ho_Chi_Minh'),
    "ecuador": timezone('America/Guayaquil'),
}


class DatetimeWithChileTZ(DatetimeWithTZ):
    country_tz = country_tz_mapping["chile"]


class DatetimeWithColombiaTZ(DatetimeWithTZ):
    country_tz = country_tz_mapping["colombia"]


class DatetimeWithMexicoTZ(DatetimeWithTZ):
    country_tz = country_tz_mapping["mexico"]


class DatetimeWithPakistanTZ(DatetimeWithTZ):
    country_tz = country_tz_mapping["pakistan"]


class DatetimeWithPeruTZ(DatetimeWithTZ):
    country_tz = country_tz_mapping["peru"]


class DatetimeWithPhilippinesTZ(DatetimeWithTZ):
    country_tz = country_tz_mapping["philippines"]


class DatetimeWithVietnamTZ(DatetimeWithTZ):
    country_tz = country_tz_mapping["vietnam"]


class DatetimeWithEcuadorTZ(DatetimeWithTZ):
    country_tz = country_tz_mapping["ecuador"]


country_to_datetime = {
    "chile": DatetimeWithChileTZ,
    "colombia": DatetimeWithColombiaTZ,
    "mexico": DatetimeWithMexicoTZ,
    "pakistan": DatetimeWithPakistanTZ,
    "peru": DatetimeWithPeruTZ,
    "philippines": DatetimeWithPhilippinesTZ,
    "vietnam": DatetimeWithVietnamTZ,
    "ecuador": DatetimeWithEcuadorTZ,
}

def return_unify_datetime_var(country: str):
    datetime = country_to_datetime[country]

    cur_time = int(time.time() * 1000)
    cur_datetime = datetime.now()
    cur_datetime_str = cur_datetime.strftime("%Y-%m-%d %H:%M:%S")
    cur_date_str = cur_datetime.strftime("%Y-%m-%d")
    cur_date_time = int(datetime.strptime(
        cur_date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S").timestamp() * 1000)

    return datetime, cur_time, cur_datetime, cur_datetime_str, cur_date_str, cur_date_time


if __name__ == "__main__":
    print(DatetimeWithMexicoTZ.now())
    current_date = DatetimeWithMexicoTZ.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    current_date_timestamp = int(current_date.timestamp() * 1000)

    cur_time = int(time.time() * 1000)
    cur_datetime = DatetimeWithMexicoTZ.now()
    cur_datetime_str = cur_datetime.strftime("%Y-%m-%d %H:%M:%S")
    cur_date_str = cur_datetime.strftime("%Y-%m-%d")
    cur_date_time = int(DatetimeWithMexicoTZ.strptime(
        cur_date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    
    print('current_date', current_date)
    print('current_date_timestamp', current_date_timestamp)

    print('cur_datetime', cur_datetime)
    print('cur_datetime_str', cur_datetime_str)
    print('cur_date_str', cur_date_str)
    print('cur_date_time', cur_date_time)

    print(return_unify_datetime_var('mexico'))