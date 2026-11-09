import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv


load_dotenv(override=True)


THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)

# Post-processing / collection-note calls use the official DeepSeek API.
DEEPSEEK_OFFICIAL_BASE_URL = "https://api.deepseek.com/v1"
# DeepSeek's OpenAI-compatible endpoint only accepts
# response_format={"type": "json_object"}; the json_schema structured-output
# type is rejected, so those hosts fall back to json_object.
JSON_SCHEMA_UNSUPPORTED_HOSTS = frozenset({"api.deepseek.com"})


def _supports_json_schema(base_url: str) -> bool:
    host = (urlparse(str(base_url or "")).hostname or "").casefold()
    return not any(
        host == candidate or host.endswith(f".{candidate}")
        for candidate in JSON_SCHEMA_UNSUPPORTED_HOSTS
    )


def _apply_json_object_fallback(body: dict[str, Any], schema: dict[str, Any]) -> None:
    """Use json_object mode and spell the schema out in the prompt.

    json_object only guarantees valid JSON syntax -- it ignores the schema -- so
    the required keys and enum values have to come from the prompt itself.
    """
    body["response_format"] = {"type": "json_object"}
    messages = body.get("messages") or []
    if not messages:
        return
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    messages[0]["content"] = (
        f"{messages[0].get('content', '')}\n"
        "Output rules:\n"
        "- Return exactly one valid JSON object and nothing else.\n"
        f"- The JSON object must match this schema: {schema_text}\n"
        "- Do not add extra keys, markdown, or code fences."
    )


def strip_think_blocks(text: str) -> str:
    cleaned = THINK_BLOCK_RE.sub("", str(text or ""))
    cleaned = THINK_TAG_RE.sub("", cleaned)
    return cleaned.strip()


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _extract_text_from_response(payload: Any) -> str:
    choices = _obj_get(payload, "choices", []) or []
    if not choices:
        raise ValueError(f"OpenAI-compatible response missing choices: {payload}")

    message = _obj_get(choices[0], "message", {}) or {}
    content = _obj_get(message, "content", "")
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                texts.append(item.get("text", ""))
            else:
                texts.append(getattr(item, "text", ""))
        content = "".join(texts)
    content = strip_think_blocks(str(content))
    if not content:
        raise ValueError(f"OpenAI-compatible response missing message content: {payload}")
    return content


def _parse_json_text(text: str) -> dict[str, Any]:
    cleaned = strip_think_blocks(text)
    fenced_json = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_json:
        cleaned = fenced_json.group(1).strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json\n", "", 1).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    parsed_objects: list[tuple[int, dict[str, Any]]] = []
    for match in re.finditer(r"\{", cleaned):
        try:
            parsed, end = decoder.raw_decode(cleaned[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            parsed_objects.append((end, parsed))
    if parsed_objects:
        return max(parsed_objects, key=lambda item: item[0])[1]

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end >= start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def _normalize_openrouter_model(model: str) -> str:
    model = (model or "").strip()
    if model in {"chat-oss-120b", "gpt-oss-120b"}:
        return "openai/gpt-oss-120b"
    return model or "openai/gpt-oss-120b"


@dataclass
class VertexAIOpenAIClient:
    project_id: str
    location: str
    timeout: float = 30.0
    model: str = "google/gemini-2.5-flash"
    credentials_path: str = ""
    credentials_json: str = ""
    _sdk_client: Any = field(default=None, init=False, repr=False)
    _credentials: Any = field(default=None, init=False, repr=False)

    @property
    def flash_model(self) -> str:
        return self.model

    @property
    def lite_model(self) -> str:
        return self.model

    @classmethod
    def from_env(cls) -> "VertexAIOpenAIClient":
        credentials_json = os.getenv("VERTEX_AI_SERVICE_ACCOUNT_JSON", "").strip()
        project_id = os.getenv("VERTEX_AI_PROJECT_ID", "").strip()
        location = os.getenv("VERTEX_AI_LOCATION", "global").strip()
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if not project_id and credentials_json:
            try:
                project_id = json.loads(credentials_json).get("project_id", "").strip()
            except json.JSONDecodeError as exc:
                raise RuntimeError("VERTEX_AI_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
        if not project_id:
            raise RuntimeError("VERTEX_AI_PROJECT_ID is required for Vertex AI collection-note workflow")

        return cls(
            project_id=project_id,
            location=location,
            timeout=float(os.getenv("VERTEX_AI_TIMEOUT", "30")),
            model=os.getenv("VERTEX_AI_MODEL", "google/gemini-2.5-flash"),
            credentials_path=credentials_path,
            credentials_json=credentials_json,
        )

    async def generate_text(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0,
        response_mime_type: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        payload = await self._generate(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
        )
        return _extract_text_from_response(payload)

    async def generate_json(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        temperature: float = 0,
    ) -> dict[str, Any]:
        payload = await self._generate(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )
        return _parse_json_text(_extract_text_from_response(payload))

    async def _generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        response_mime_type: str | None,
        response_schema: dict[str, Any] | None,
    ) -> Any:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }

        if response_mime_type == "application/json" and response_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "collection_note_result",
                    "strict": True,
                    "schema": response_schema,
                },
            }

        client = self._get_sdk_client()
        await self._refresh_access_token_if_needed()
        return await client.chat.completions.create(**body)

    def _get_sdk_client(self):
        if self._sdk_client is not None:
            return self._sdk_client

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is required. Install it with `pip install openai`."
            ) from exc

        self._sdk_client = AsyncOpenAI(
            api_key="PLACEHOLDER",
            base_url=(
                f"https://aiplatform.googleapis.com/v1/projects/"
                f"{self.project_id}/locations/{self.location}/endpoints/openapi"
            ),
            timeout=self.timeout,
        )
        return self._sdk_client

    def _get_credentials(self):
        if self._credentials is not None:
            return self._credentials

        try:
            import google.auth
            from google.oauth2 import service_account
        except ImportError as exc:
            raise RuntimeError(
                "google-auth is required for Vertex AI auth. Install it with `pip install google-auth`."
            ) from exc

        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        if self.credentials_json:
            try:
                info = json.loads(self.credentials_json)
            except json.JSONDecodeError as exc:
                raise RuntimeError("VERTEX_AI_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
            self._credentials = service_account.Credentials.from_service_account_info(
                info,
                scopes=scopes,
            )
        elif self.credentials_path:
            self._credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=scopes,
            )
        else:
            credentials, _ = google.auth.default(scopes=scopes)
            self._credentials = credentials
        return self._credentials

    async def _refresh_access_token_if_needed(self) -> None:
        credentials = self._get_credentials()
        if not credentials.valid:
            try:
                import google.auth.transport.requests
            except ImportError as exc:
                raise RuntimeError(
                    "google-auth is required for Vertex AI auth. Install it with `pip install google-auth`."
                ) from exc
            request = google.auth.transport.requests.Request()
            credentials.refresh(request)

        if not credentials.valid or not credentials.token:
            raise RuntimeError("Unable to refresh Vertex AI access token")

        self._sdk_client.api_key = credentials.token


@dataclass
class OpenRouterOpenAIClient:
    """OpenAI-compatible client used by post-call processing workflows.

    The class name is kept for backwards-compatible imports.  Configuration is
    provider-neutral and defaults to the official DeepSeek API.
    """

    api_key: str
    base_url: str = DEEPSEEK_OFFICIAL_BASE_URL
    timeout: float = 30.0
    model: str = "deepseek-flash"
    _sdk_client: Any = field(default=None, init=False, repr=False)

    @property
    def flash_model(self) -> str:
        return self.model

    @property
    def lite_model(self) -> str:
        return self.model

    @classmethod
    def from_env(cls) -> "OpenRouterOpenAIClient":
        api_key = (
            os.getenv("POST_PROCESS_LLM_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or ""
        ).strip()
        if not api_key:
            raise RuntimeError(
                "POST_PROCESS_LLM_API_KEY or DEEPSEEK_API_KEY is required for the "
                "post-processing collection-note workflow"
            )

        return cls(
            api_key=api_key,
            base_url=os.getenv("POST_PROCESS_LLM_BASE_URL", DEEPSEEK_OFFICIAL_BASE_URL).strip(),
            timeout=float(os.getenv("POST_PROCESS_LLM_TIMEOUT", "30")),
            model=os.getenv("POST_PROCESS_LLM_MODEL", "deepseek-flash").strip(),
        )

    async def generate_text(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0,
        response_mime_type: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        payload = await self._generate(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
        )
        return _extract_text_from_response(payload)

    async def generate_json(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        temperature: float = 0,
    ) -> dict[str, Any]:
        payload = await self._generate(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )
        return _parse_json_text(_extract_text_from_response(payload))

    async def generate_json_with_usage(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        temperature: float = 0,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        payload = await self._generate(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        )
        parsed = _parse_json_text(_extract_text_from_response(payload))
        usage = _obj_get(payload, "usage", {}) or {}
        usage_summary = {
            "prompt_tokens": int(_obj_get(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(_obj_get(usage, "completion_tokens", 0) or 0),
            "cache_read_input_tokens": int(_obj_get(usage, "cache_read_input_tokens", 0) or 0),
        }
        logging.info(
            "OpenRouter usage summary: model=%s usage_present=%s prompt_tokens=%s completion_tokens=%s cache_read_input_tokens=%s",
            _normalize_openrouter_model(model or self.model),
            bool(usage),
            usage_summary["prompt_tokens"],
            usage_summary["completion_tokens"],
            usage_summary["cache_read_input_tokens"],
        )
        return parsed, usage_summary

    async def _generate(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        response_mime_type: str | None,
        response_schema: dict[str, Any] | None,
    ) -> Any:
        body: dict[str, Any] = {
            "model": _normalize_openrouter_model(model or self.model),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }

        uses_json_schema = False
        if response_mime_type == "application/json" and response_schema:
            if _supports_json_schema(self.base_url):
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "collection_note_result",
                        "strict": True,
                        "schema": response_schema,
                    },
                }
                uses_json_schema = True
            else:
                _apply_json_object_fallback(body, response_schema)

        client = self._get_sdk_client()
        try:
            return await client.chat.completions.create(**body)
        except Exception as exc:
            # Some OpenAI-compatible endpoints reject json_schema at runtime even
            # though their host suggests support; degrade to json_object.
            if not uses_json_schema or "response_format" not in str(exc).casefold():
                raise
            logging.warning(
                "response_format=json_schema rejected by %s (%s); retrying with json_object",
                self.base_url,
                exc,
            )
            _apply_json_object_fallback(body, response_schema or {})
            return await client.chat.completions.create(**body)

    def _get_sdk_client(self):
        if self._sdk_client is not None:
            return self._sdk_client

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is required. Install it with `pip install openai`."
            ) from exc

        self._sdk_client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )
        return self._sdk_client


async def _run_who_are_you_demo() -> None:
    client = VertexAIOpenAIClient.from_env()
    response = await client.generate_text(
        model=client.flash_model,
        system_prompt="请简洁回答用户问题。",
        user_prompt="你是谁？",
        temperature=0,
    )
    print(response)


if __name__ == "__main__":
    try:
        asyncio.run(_run_who_are_you_demo())
    except Exception as exc:
        print(f"Demo failed: {exc}")
