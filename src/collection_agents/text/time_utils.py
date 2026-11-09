"""时区与时间工具（由 my_utils/utc2tz.py 与 my_utils/datetime_with_tz.py 合并）。"""
import logging
import time
from datetime import datetime, tzinfo

import pytz
from pytz import timezone


def utc2tz(utc_time, country="mexico"):
    """将时间戳转换为时区对应时间
    Args:
        utc_time: python时间戳，单位ms
        country:国家，目前支持墨西哥，哥伦比亚，印度，埃及，等
    Returns:
        datetime:返回格式化后的时间字符串
    """

    utc_time = int(utc_time)
    tz = timezone('America/Mexico_City')
    if country == "mexico" or country == "MX" :
        tz = timezone('America/Mexico_City')
    elif country == "colombia" or country == "CO" :
        tz = timezone('America/Bogota')
    elif country == "india" or country == "IN":
        tz = timezone('Asia/Kolkata')
    elif country == "egypt" or country == "EG":
        tz = timezone('Africa/Cairo')
    elif country == "philippines" or country == "PH":
        tz = timezone('Asia/Manila')
    elif country == "brazil" or country == "BR":
        tz = timezone('America/Sao_Paulo')
    elif country == "peru" or country == "PE":
        tz = timezone('America/Lima')
    elif country == "chile" or country == "CL":
        tz = timezone('America/Santiago')
    dt = pytz.datetime.datetime.fromtimestamp(utc_time, tz)
    logging.info(f"Timezone: {tz.zone}, contact_time: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
    return dt.strftime('%Y-%m-%d %H:%M:%S')


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
    datetime_cls = country_to_datetime[country]

    cur_time = int(time.time() * 1000)
    cur_datetime = datetime_cls.now()
    cur_datetime_str = cur_datetime.strftime("%Y-%m-%d %H:%M:%S")
    cur_date_str = cur_datetime.strftime("%Y-%m-%d")
    cur_date_time = int(datetime_cls.strptime(
        cur_date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S").timestamp() * 1000)

    return datetime_cls, cur_time, cur_datetime, cur_datetime_str, cur_date_str, cur_date_time
