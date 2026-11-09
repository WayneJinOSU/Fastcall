"""兼容 shim：已迁移至 notes/call_notes.py。"""
from notes.call_notes import *  # noqa: F401,F403
from notes.call_notes import (  # noqa: F401
    build_context_messages_with_timestamps,
    intelligent_collection_notes,
    intelligent_notes_distribution,
    save_conversation,
    strip_harness_prompt_system_messages,
)
