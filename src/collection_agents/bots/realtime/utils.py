import time

from pipecat.processors.aggregators.llm_context import LLMContext


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _summarize_for_log(text: str, limit: int = 180) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _count_realtime_transcript_units(text: str) -> int:
    count = 0
    in_word = False
    for char in text or "":
        if "\u3400" <= char <= "\u9fff":
            count += 1
            in_word = False
        elif char.isalnum():
            if not in_word:
                count += 1
                in_word = True
        else:
            in_word = False
    return count


def _is_effective_realtime_transcript(text: str) -> bool:
    return _count_realtime_transcript_units(text) > 2


def _append_context_message_without_llm(context: LLMContext, message: dict):
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
