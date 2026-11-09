import logging

from pytz import timezone
import pytz

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