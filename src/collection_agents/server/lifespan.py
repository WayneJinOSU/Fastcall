"""FastAPI lifespan：启动时预热 Vosk 模型，避免第一通电话丢音频。

Vosk's Model() cold load takes ~15s locally (longer on network storage). The
model is cached process-wide in stt_local._vosk_model_cache, so loading it once
here means every subsequent call hits the cache and is ready instantly. Runs as
a background task to avoid blocking server startup; failures are logged, not
raised, so a missing model/vosk never prevents the app from serving traffic.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Default Vosk model path used by create_stt_service (services/providers.py).
# Override via VOSK_MODEL_PATH env if the deployment mounts the model elsewhere.
DEFAULT_VOSK_MODEL_PATH = "/mnt/data/vosk-model-small-es-0.42"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preheat the Vosk model at startup so the first call doesn't drop audio."""

    async def _preheat_vosk():
        model_path = os.getenv("VOSK_MODEL_PATH", DEFAULT_VOSK_MODEL_PATH)
        if not os.path.isdir(model_path):
            logging.info(f"[Vosk preheat] skipped, path not found: {model_path}")
            return
        try:
            from collection_agents.services.stt_local import _get_or_load_model

            logging.info(f"[Vosk preheat] loading model from {model_path} ...")
            t0 = time.perf_counter()
            await _get_or_load_model(model_path)
            logging.info(f"[Vosk preheat] model ready in {time.perf_counter() - t0:.2f}s")
        except Exception as exc:
            logging.warning(f"[Vosk preheat] failed (will retry on first call): {exc}")

    preheat_task = asyncio.create_task(_preheat_vosk())
    try:
        yield
    finally:
        if not preheat_task.done():
            preheat_task.cancel()
