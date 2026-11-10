import asyncio
import json
import logging
import os
import time
from urllib.parse import urlencode

import websockets
from pipecat.services.openai.realtime.events import (
    SessionProperties,
    SessionUpdateEvent,
)

from collection_agents.bots.realtime.resources import _build_realtime_base_url


_WARMUP_TIMEOUT_SECS = 8.0
_WARMUP_ATTEMPTS = 2
_WARMUP_RETRY_DELAY_SECS = 0.5


def _warmup_enabled() -> bool:
    value = os.getenv("STARTUP_PROVIDER_WARMUP", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


async def _warm_azure_realtime():
    endpoint = os.getenv("AZURE_REALTIME_ENDPOINT", "").strip()
    model = os.getenv("AZURE_REALTIME_MODEL", "").strip()
    api_key = os.getenv("AZURE_REALTIME_API_KEY", "").strip()
    if not endpoint or not model or not api_key:
        return "skipped"

    url = _build_realtime_base_url(endpoint, model)
    async with websockets.connect(
        url,
        additional_headers={"api-key": api_key},
        open_timeout=_WARMUP_TIMEOUT_SECS,
        max_size=None,
    ) as websocket:
        event = SessionUpdateEvent(
            session=SessionProperties(
                output_modalities=["audio"],
                instructions="startup warmup",
            )
        )
        await websocket.send(json.dumps(event.model_dump(exclude_none=True)))
        deadline = asyncio.get_running_loop().time() + _WARMUP_TIMEOUT_SECS
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Azure Realtime session.updated not received")
            evt = json.loads(await asyncio.wait_for(websocket.recv(), remaining))
            if evt.get("type") == "session.updated":
                return "ready"
            if evt.get("type") == "error":
                raise RuntimeError(f"Azure Realtime warmup error: {evt.get('error')}")


async def _warm_deepgram():
    api_key = os.getenv("DEEPGRAM_API_KEY", "").strip()
    if not api_key:
        return "skipped"
    query = urlencode(
        {
            "encoding": "linear16",
            "language": "en-US",
            "model": "nova-2",
            "sample_rate": 8000,
            "channels": 1,
        }
    )
    async with websockets.connect(
        f"wss://api.deepgram.com/v1/listen?{query}",
        additional_headers={"Authorization": f"Token {api_key}"},
        open_timeout=_WARMUP_TIMEOUT_SECS,
    ):
        return "connected"


async def _warm_cartesia():
    api_key = os.getenv("CARTESIA_API_KEY", "").strip()
    if not api_key:
        return "skipped"
    query = urlencode(
        {
            "api_key": api_key,
            "cartesia_version": "2025-04-16",
        }
    )
    async with websockets.connect(
        f"wss://api.cartesia.ai/tts/websocket?{query}",
        open_timeout=_WARMUP_TIMEOUT_SECS,
    ):
        return "connected"


async def warm_provider_connections() -> dict[str, str]:
    if not _warmup_enabled():
        logging.info("Provider startup warmup disabled.")
        return {}

    started = time.perf_counter()
    providers = {
        "azure_realtime": _warm_azure_realtime,
        "deepgram": _warm_deepgram,
        "cartesia": _warm_cartesia,
    }
    async def run_provider(name: str, provider):
        last_error = None
        for attempt in range(1, _WARMUP_ATTEMPTS + 1):
            try:
                return await provider()
            except Exception as exc:
                last_error = exc
                if attempt < _WARMUP_ATTEMPTS:
                    logging.info(
                        "Provider startup warmup retry: %s attempt=%d error=%s",
                        name,
                        attempt + 1,
                        exc,
                    )
                    await asyncio.sleep(_WARMUP_RETRY_DELAY_SECS)
        raise last_error

    results = await asyncio.gather(
        *(run_provider(name, provider) for name, provider in providers.items()),
        return_exceptions=True,
    )
    statuses = {}
    for (name, _), result in zip(providers.items(), results):
        if isinstance(result, Exception):
            statuses[name] = f"failed:{type(result).__name__}"
            logging.warning("Provider startup warmup failed: %s: %s", name, result)
        else:
            statuses[name] = str(result)
    logging.info(
        "Provider startup warmup complete in %.2fs: %s",
        time.perf_counter() - started,
        statuses,
    )
    return statuses


async def provider_warmup_loop(interval_secs: float) -> None:
    if interval_secs <= 0:
        return
    while True:
        await asyncio.sleep(interval_secs)
        try:
            await warm_provider_connections()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.warning("Periodic provider warmup failed: %s", exc)
