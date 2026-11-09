"""兼容 shim：已迁移至 services/vosk_stt.py。"""
from services.vosk_stt import *  # noqa: F401,F403
from services.vosk_stt import VoskSTTService, _get_or_load_model  # noqa: F401
