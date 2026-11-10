"""对话提取：把 pipecat LLMContext 转成带时间戳的消息列表与对话对。

供纪要分发（call_notes）使用。
"""

import logging
import time

from collection_agents.services.call_config import is_synthetic_first_question


def _parse_timestamp(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_message_timestamp_ms(message, message_index, context_timestamps=None):
    if isinstance(message, dict):
        ts = _parse_timestamp(message.get("timestamp"))
        if ts is not None:
            return ts

    if isinstance(context_timestamps, list) and 0 <= message_index < len(context_timestamps):
        ts = _parse_timestamp(context_timestamps[message_index])
        if ts is not None:
            return ts

    return int(time.time() * 1000 + message_index)


def build_context_messages_with_timestamps(context):
    messages = getattr(context, "messages", None) if context else None
    if not isinstance(messages, list) and context:
        try:
            if hasattr(context, "get_messages"):
                messages = context.get_messages()
            elif hasattr(context, "get_messages_for_persistent_storage"):
                messages = context.get_messages_for_persistent_storage()
        except Exception as exc:
            logging.error(f"Error reading context messages: {exc}")
            messages = []
    if not isinstance(messages, list):
        return []

    context_timestamps = getattr(context, "_message_timestamps_ms", [])
    context_timestamps = context_timestamps if isinstance(context_timestamps, list) else []

    enriched_messages = []
    for idx, message in enumerate(messages):
        if isinstance(message, dict):
            message_copy = dict(message)
            message_copy.setdefault(
                "timestamp",
                _resolve_message_timestamp_ms(message_copy, idx, context_timestamps=context_timestamps),
            )
            enriched_messages.append(message_copy)
        else:
            enriched_messages.append(message)

    return enriched_messages


def strip_harness_prompt_system_messages(messages, metrics_collector=None):
    """Omit the one-attempt harness prompt from persisted conversation data."""
    if isinstance(metrics_collector, dict):
        prompt_uuid = metrics_collector.get("prompt_uuid", "")
    else:
        prompt_uuid = getattr(metrics_collector, "prompt_uuid", "")
    if not prompt_uuid:
        return messages
    return [
        message
        for message in messages
        if not (isinstance(message, dict) and message.get("role") == "system")
    ]


def extract_dialogs(context, user_speaker="user", skip_prompt_prefix=False):
    dialogs = []
    messages = getattr(context, "messages", None)
    if not isinstance(messages, list):
        return dialogs
    context_timestamps = getattr(context, "_message_timestamps_ms", [])
    context_timestamps = context_timestamps if isinstance(context_timestamps, list) else []

    for idx, msg in enumerate(messages[1:]):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ["user", "assistant"]:
            continue

        speaker = user_speaker if role == "user" else "bot"
        content = msg.get("content", "")
        message_index = idx + 1
        timestamp_ms = _resolve_message_timestamp_ms(
            msg,
            message_index,
            context_timestamps=context_timestamps,
        )

        if idx in (0, 1):
            if speaker == user_speaker and is_synthetic_first_question(content):
                continue
            if speaker == "bot" and content:
                dialogs.append(
                    {
                        "speaker": speaker,
                        "content": content,
                        "timestamp": timestamp_ms,
                    }
                )
                continue

        content = content.replace("LLMFullResponseEndFrame", "")
        if content and (not skip_prompt_prefix or not content.startswith("请说：")):
            dialogs.append(
                {
                    "speaker": speaker,
                    "content": content,
                    "timestamp": timestamp_ms,
                }
            )

    return dialogs

