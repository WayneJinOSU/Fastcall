from collection_agents.services.realtime_context import (
    append_context_message_without_llm as _append_context_message_without_llm,
    summarize_for_log as _summarize_for_log,
)


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
