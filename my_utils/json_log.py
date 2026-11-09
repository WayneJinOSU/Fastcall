"""本地 JSON 日志实现，替代内部包 efk_log。

提供与 efk_log 兼容的最小接口：

- ``LogJsonFormat(file_path=None, console=True, project="...")``
  为 root logger 安装 JSON 格式化 handler（控制台和/或文件）。
- ``set_trace_code(code)``
  设置当前上下文的链路追踪码，后续日志记录的 JSON 中会带上
  ``trace_code`` 字段（基于 contextvars，异步任务间隔离）。

仅依赖标准库，方便开源自部署。
"""

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

_trace_code: contextvars.ContextVar[str] = contextvars.ContextVar(
    "efk_trace_code", default=""
)


def set_trace_code(code: str | None) -> None:
    """设置当前上下文的 trace code（None/空字符串表示清除）。"""
    _trace_code.set(code or "")


def get_trace_code() -> str:
    return _trace_code.get()


class _TraceCodeFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "trace_code", None):
            record.trace_code = _trace_code.get()
        return True


class JsonFormatter(logging.Formatter):
    """把 LogRecord 序列化为单行 JSON。"""

    def __init__(self, project: str = "app"):
        super().__init__()
        self.project = project

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "project": self.project,
            "logger": record.name,
            "source": f"{record.filename}:{record.funcName}:{record.lineno}",
            "message": record.getMessage(),
        }
        trace_code = getattr(record, "trace_code", "")
        if trace_code:
            payload["trace_code"] = trace_code
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class LogJsonFormat:
    """efk_log.LogJsonFormat 的兼容实现：调用即完成 root logger 配置。"""

    def __init__(
        self,
        file_path: str | None = None,
        console: bool = True,
        project: str = "app",
        level: int = logging.DEBUG,
    ):
        formatter = JsonFormatter(project=project)
        trace_filter = _TraceCodeFilter()

        root = logging.getLogger()
        root.setLevel(level)

        # 避免重复安装（例如热重载场景）
        for handler in list(root.handlers):
            if getattr(handler, "_json_log_managed", False):
                root.removeHandler(handler)

        if console:
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            stream_handler.addFilter(trace_filter)
            stream_handler._json_log_managed = True  # type: ignore[attr-defined]
            root.addHandler(stream_handler)

        if file_path:
            file_handler = logging.FileHandler(file_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.addFilter(trace_filter)
            file_handler._json_log_managed = True  # type: ignore[attr-defined]
            root.addHandler(file_handler)

        if not console and not file_path:
            # 保证至少有一个出口，避免 "No handlers could be found" 类问题
            fallback = logging.StreamHandler(sys.stdout)
            fallback.setFormatter(formatter)
            fallback.addFilter(trace_filter)
            fallback._json_log_managed = True  # type: ignore[attr-defined]
            root.addHandler(fallback)
