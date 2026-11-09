"""兼容 shim：已迁移至 services/providers.py。"""
from services.providers import *  # noqa: F401,F403
from services.providers import (  # noqa: F401
    attach_llm_retry_counter,
    create_llm_service,
    create_stt_service,
    create_tts_service,
    load_output_sounds,
    resolve_bot_llm_runtime,
    resolve_deepgram_model,
    resolve_deepgram_shadow_config,
    resolve_stt_provider,
    select_azure_llm_target,
    send_hangup_signal,
)
