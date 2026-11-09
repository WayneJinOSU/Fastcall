"""兼容 shim：已迁移至 services/shadow_stt.py。"""
from services.shadow_stt import *  # noqa: F401,F403
from services.shadow_stt import (  # noqa: F401
    DeepgramShadowDecoder,
    ShadowDecodeTap,
    VoskFinalCollector,
    _DEEPGRAM_AVAILABLE,
)
