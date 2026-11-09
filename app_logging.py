"""兼容 shim：已迁移至 infra/logging.py，请使用新路径。"""
from infra.logging import (
    AppLogFilter,
    LogJsonFormat,
    configure_logging,
    get_trace_code,
    set_request_trace_code,
    set_trace_code,
)

__all__ = [
    "AppLogFilter",
    "LogJsonFormat",
    "configure_logging",
    "get_trace_code",
    "set_request_trace_code",
    "set_trace_code",
]
