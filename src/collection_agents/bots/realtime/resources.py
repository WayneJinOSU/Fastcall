import asyncio
import logging
import os
from urllib.parse import urlencode, urlsplit, urlunsplit


def _build_realtime_base_url(endpoint: str, model: str) -> str:
    parts = urlsplit(endpoint.strip())
    if parts.scheme != "wss":
        raise ValueError(
            f"Azure realtime endpoint must start with wss://, got: {endpoint}"
        )

    path = parts.path.rstrip("/")
    if not path or path == "/":
        path = "/openai/v1/realtime"

    return urlunsplit(
        (parts.scheme, parts.netloc, path, urlencode({"model": model.strip()}), "")
    )


class RealtimeResourceManager:
    def __init__(self):
        self._resource = self._load_resource()
        self._active_count = 0
        self._lock = asyncio.Lock()

    def _load_resource(self) -> dict:
        endpoint = os.getenv("AZURE_REALTIME_ENDPOINT", "").strip()
        model = os.getenv("AZURE_REALTIME_MODEL", "").strip()
        api_key = os.getenv("AZURE_REALTIME_API_KEY", "").strip()
        if not endpoint or not model or not api_key:
            raise ValueError(
                "Azure realtime requires AZURE_REALTIME_ENDPOINT, "
                "AZURE_REALTIME_MODEL and AZURE_REALTIME_API_KEY."
            )

        resource = {
            "id": "env-default",
            "endpoint": endpoint,
            "base_url": _build_realtime_base_url(endpoint, model),
            "model": model,
            "asr_model": os.getenv(
                "AZURE_REALTIME_TRANSCRIPTION_MODEL",
                "whisper-1",
            ).strip(),
            "api_key": api_key,
        }
        logging.info(
            "Loaded Azure realtime resource: id=%s model=%s asr_model=%s",
            resource["id"],
            resource["model"],
            resource["asr_model"],
        )
        return resource

    async def acquire(self) -> dict:
        async with self._lock:
            self._active_count += 1
            logging.info(
                "Acquired Azure realtime resource: id=%s active=%s",
                self._resource["id"],
                self._active_count,
            )
            return self._resource

    async def release(self, resource: dict):
        if not resource:
            return
        async with self._lock:
            if resource.get("id") == self._resource["id"]:
                self._active_count = max(0, self._active_count - 1)
                logging.info(
                    "Released Azure realtime resource: id=%s active=%s",
                    resource["id"],
                    self._active_count,
                )


_realtime_resource_manager = None


def get_realtime_resource_manager() -> RealtimeResourceManager:
    global _realtime_resource_manager
    if _realtime_resource_manager is None:
        _realtime_resource_manager = RealtimeResourceManager()
    return _realtime_resource_manager
