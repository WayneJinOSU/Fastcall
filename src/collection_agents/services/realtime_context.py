import time

from pipecat.processors.aggregators.llm_context import LLMContext


def summarize_for_log(text: str, limit: int = 180) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def append_context_message_without_llm(context: LLMContext, message: dict):
    context.add_message(message)
    messages = getattr(context, "messages", None)
    if not isinstance(messages, list):
        return
    timestamps = getattr(context, "_message_timestamps_ms", None)
    timestamps = list(timestamps) if isinstance(timestamps, list) else []
    now_ms = int(time.time() * 1000)
    while len(timestamps) < len(messages) - 1:
        last_ms = timestamps[-1] if timestamps else now_ms - 1
        timestamps.append(max(now_ms, last_ms + 1))
    if len(timestamps) < len(messages):
        last_ms = timestamps[-1] if timestamps else now_ms - 1
        timestamps.append(max(now_ms, last_ms + 1))
    setattr(context, "_message_timestamps_ms", timestamps[: len(messages)])
