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
        self._resources = [self._load_resource()]
        self._active_counts = {resource["id"]: 0 for resource in self._resources}
        self._assigned_counts = {resource["id"]: 0 for resource in self._resources}
        self._lock = asyncio.Lock()
        self._cursor = 0

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
            "weight": 1,
            "max_concurrency": 1000000,
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

            def resource_score(resource: dict):
                resource_id = resource["id"]
                weight = resource["weight"]
                return (
                    self._active_counts[resource_id] / weight,
                    self._assigned_counts[resource_id] / weight,
                )

            min_score = min(resource_score(resource) for resource in self._resources)
            best_candidates = [
                resource
                for resource in self._resources
                if resource_score(resource) == min_score
            ]
            selected = best_candidates[self._cursor % len(best_candidates)]
            self._cursor += 1
            self._active_counts[selected["id"]] += 1
            self._assigned_counts[selected["id"]] += 1
            active = self._active_counts[selected["id"]]
            assigned = self._assigned_counts[selected["id"]]
            max_concurrency = selected["max_concurrency"]
            if active > max_concurrency:
                logging.warning(
                    "Azure realtime resource above configured max_concurrency: "
                    "id=%s active=%s max_concurrency=%s",
                    selected["id"],
                    active,
                    max_concurrency,
                )
            logging.info(
                "Acquired Azure realtime resource: id=%s active=%s assigned=%s",
                selected["id"],
                active,
                assigned,
            )
            return selected

    async def release(self, resource: dict):
        if not resource:
            return
        async with self._lock:
            resource_id = resource.get("id")
            if resource_id in self._active_counts:
                self._active_counts[resource_id] = max(
                    0,
                    self._active_counts[resource_id] - 1,
                )
                logging.info(
                    "Released Azure realtime resource: id=%s active=%s",
                    resource_id,
                    self._active_counts[resource_id],
                )


_realtime_resource_manager = None


def get_realtime_resource_manager() -> RealtimeResourceManager:
    global _realtime_resource_manager
    if _realtime_resource_manager is None:
        _realtime_resource_manager = RealtimeResourceManager()
    return _realtime_resource_manager
