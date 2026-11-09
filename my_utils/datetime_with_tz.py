"""兼容 shim：已迁移至 text/time_utils.py。"""
from text.time_utils import (  # noqa: F401
    DatetimeWithChileTZ,
    DatetimeWithColombiaTZ,
    DatetimeWithEcuadorTZ,
    DatetimeWithMexicoTZ,
    DatetimeWithPakistanTZ,
    DatetimeWithPeruTZ,
    DatetimeWithPhilippinesTZ,
    DatetimeWithTZ,
    DatetimeWithVietnamTZ,
    country_to_datetime,
    country_tz_mapping,
    return_unify_datetime_var,
)
