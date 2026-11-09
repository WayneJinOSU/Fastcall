import contextvars
import logging
import os
import uuid

from my_utils.json_log import LogJsonFormat, set_trace_code
from loguru import logger as loguru_logger


_current_trace_code: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_trace_code",
    default=None,
)


class AppLogFilter(logging.Filter):
    """
    Keep core business/debug logs by default while gating high-volume traces
    behind targeted env flags.

    Flags:
    - `APP_DEBUG_VOICE=1`: keep Pipecat turn-taking/perf/TTS chunk debug logs.
    - `APP_DEBUG_LLM=1`: keep full LLM prompt/context debug logs.
    - `APP_DEBUG_HTTP=1`: keep HTTP/OpenAI client wire trace logs.
    """

    def __init__(self):
        super().__init__()
        self._debug_voice = os.getenv("APP_DEBUG_VOICE", "0") == "1"
        self._debug_llm = os.getenv("APP_DEBUG_LLM", "0") == "1"
        self._debug_http = os.getenv("APP_DEBUG_HTTP", "0") == "1"

    @staticmethod
    def _is_http_trace_noise(record: logging.LogRecord, msg: str) -> bool:
        if record.name.startswith(("httpcore", "httpx", "openai._trace")):
            return True

        if record.filename == "_trace.py" and record.funcName == "atrace":
            return True

        return any(
            marker in msg
            for marker in (
                "send_request_headers.",
                "send_request_body.",
                "receive_response_headers.",
                "receive_response_body.",
                "response_closed.",
            )
        )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.DEBUG:
            return False

        if record.levelno == logging.DEBUG:
            msg = record.getMessage()

            # HTTP/OpenAI transport wire traces are almost never useful in normal debugging.
            if not self._debug_http and self._is_http_trace_noise(record, msg):
                return False

            # Full prompt/context payloads are useful only for prompt engineering/debugging.
            if not self._debug_llm and (
                "Request options:" in msg
                or "[pipecat.services.openai.base_llm:_stream_chat_completions_universal_context:" in msg
                or "request_id:" in msg
            ):
                return False

            # Voice-chain details are useful when debugging turn-taking, latency or TTS chunking.
            if not self._debug_voice and any(
                pattern in msg
                for pattern in (
                    "[pipecat.services.tts_service:push_frame:",
                    "[pipecat.processors.aggregators.llm_response_universal:_on_user_turn_started:",
                    "[pipecat.processors.aggregators.llm_response_universal:_on_user_turn_stopped:",
                    "[pipecat.processors.frame_processor:link:",
                    "[pipecat.processors.metrics.frame_processor_metrics:start_tts_usage_metrics:",
                )
            ):
                return False

        # Suppress extremely verbose websocket wire dumps such as:
        # "> BINARY ...", "< TEXT ...", which are not useful for business troubleshooting.
        if (
            record.levelno == logging.DEBUG
            and record.name.startswith("websockets.")
            and record.filename == "protocol.py"
            and record.funcName in {"send_frame", "parse"}
        ):
            msg = record.getMessage()
            if msg.startswith(("> BINARY", "< BINARY", "> TEXT", "< TEXT")):
                return False

        return True


def _bridge_loguru_to_logging() -> None:
    """
    Route loguru logs (including Pipecat internals) into stdlib logging so they
    use the local JSON formatter/handlers (my_utils.json_log).
    """

    def _inject_trace_code(record: dict) -> None:
        trace_code = _current_trace_code.get()
        if trace_code:
            record["extra"]["trace_code"] = trace_code

    class _LoguruSink:
        def __init__(self, logger_name: str = "loguru"):
            self._logger = logging.getLogger(logger_name)

        def __call__(self, message):
            record = message.record
            trace_code = record["extra"].get("trace_code")
            if trace_code:
                set_trace_code(trace_code)
            source = f"{record['name']}:{record['function']}:{record['line']}"
            text = f"[{source}] {record['message']}"
            self._logger.log(record["level"].no, text)

    # Remove default human-readable sink to avoid duplicate non-JSON logs.
    loguru_logger.remove()
    loguru_logger.configure(patcher=_inject_trace_code)
    loguru_logger.add(
        _LoguruSink(),
        level="DEBUG",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )


def configure_logging() -> None:
    """
    Keep the local JSON formatter/handlers and allow DEBUG/INFO/WARNING/ERROR.
    """
    LogJsonFormat(file_path=None, console=True, project="collection-service")
    _bridge_loguru_to_logging()

    log_level = logging.DEBUG
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    for handler in root_logger.handlers:
        handler.setLevel(log_level)
        handler.filters = [
            f for f in handler.filters if not isinstance(f, AppLogFilter)
        ]
        handler.addFilter(AppLogFilter())

    # Suppress noisy websocket transport debug traces (PING/PONG/BINARY/CLOSE/EOF).
    # Keep INFO+ from websockets so real warnings/errors remain visible.
    logging.getLogger("websockets").setLevel(logging.INFO)

    debug_http = os.getenv("APP_DEBUG_HTTP", "0") == "1"
    debug_llm = os.getenv("APP_DEBUG_LLM", "0") == "1"

    # Keep third-party client libraries quiet by default, but allow targeted
    # opt-in when debugging HTTP transport or LLM request construction.
    if not (debug_http or debug_llm):
        logging.getLogger("openai").setLevel(logging.INFO)
        logging.getLogger("openai._trace").setLevel(logging.INFO)
    if not debug_http:
        logging.getLogger("httpx").setLevel(logging.INFO)
        logging.getLogger("httpcore").setLevel(logging.INFO)


def set_request_trace_code(trace_code: str | None = None) -> None:
    # Generate a stable trace code ourselves so stdlib logging and loguru share it.
    resolved_trace_code = trace_code or str(uuid.uuid4())
    _current_trace_code.set(resolved_trace_code)
    set_trace_code(resolved_trace_code)
    return resolved_trace_code
