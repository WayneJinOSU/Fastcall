from dataclasses import dataclass

from robot_config_service.domain.contract import (
    add_issue,
    is_number,
    validate_base_contract,
    validate_object_fields,
)
from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.domain.hashing import calculate_config_hash
from robot_config_service.domain.runtime import RuntimeResolution
from robot_config_service.domain.variables import (
    validate_no_secrets,
    validate_variable_references,
    validate_variables,
)


PROMPT_FIELDS = {"system_template": "string"}
CALL_POLICY_FIELDS = {
    "allow_interruptions": "boolean",
    "max_call_duration_sec": "integer",
}
STANDARD_LLM_FIELDS = {
    "provider": "string",
    "model": "string",
    "temperature": "number",
    "top_p": "number",
    "context_rounds": "integer",
    "max_tokens": "integer",
    "timeout_ms": "integer",
}


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: list
    warnings: list
    catalog_version: str
    runtime_resolution: RuntimeResolution | None = None
    config_hash: str | None = None

    def as_dict(self) -> dict:
        result = {
            "valid": self.valid,
            "errors": [issue.as_dict() for issue in self.errors],
            "warnings": self.warnings,
            "catalog_version": self.catalog_version,
        }
        if self.runtime_resolution is not None:
            result["runtime_resolution"] = self.runtime_resolution.as_dict()
        if self.config_hash is not None:
            result["config_hash"] = self.config_hash
        return result


def _validate_flow(config, issues, standard: bool) -> None:
    flow = config.get("flow")
    if not isinstance(flow, dict):
        add_issue(issues, "flow", "Must be an object")
        return
    if not standard:
        return
    for node in ("welcomeNode", "endNode", "noResponse", "voicemailNode"):
        if not isinstance(flow.get(node), dict):
            add_issue(issues, f"flow.{node}", "Must be an object")
    welcome = flow.get("welcomeNode") if isinstance(flow.get("welcomeNode"), dict) else {}
    end = flow.get("endNode") if isinstance(flow.get("endNode"), dict) else {}
    no_response = flow.get("noResponse") if isinstance(flow.get("noResponse"), dict) else {}
    voicemail = flow.get("voicemailNode") if isinstance(flow.get("voicemailNode"), dict) else {}
    if set(welcome) - {"content"}:
        add_issue(issues, "flow.welcomeNode", "Only content is supported; silence is handled by noResponse")
    if not isinstance(welcome.get("content"), str) or not welcome["content"].strip():
        add_issue(issues, "flow.welcomeNode.content", "Must be a non-empty string")
    if not isinstance(end.get("content"), str) or not end["content"].strip():
        add_issue(issues, "flow.endNode.content", "Must be a non-empty string")
    if not isinstance(end.get("hangUpConfig"), dict):
        add_issue(issues, "flow.endNode.hangUpConfig", "Must be an object")
    if not isinstance(no_response.get("timeoutSec"), int) or isinstance(no_response.get("timeoutSec"), bool) or no_response.get("timeoutSec", 0) <= 0:
        add_issue(issues, "flow.noResponse.timeoutSec", "Must be a positive integer")
    if not isinstance(no_response.get("noResponseReply"), str) or not no_response["noResponseReply"].strip():
        add_issue(issues, "flow.noResponse.noResponseReply", "Must be a non-empty string")
    if not isinstance(no_response.get("maxNoResponseCount"), int) or isinstance(no_response.get("maxNoResponseCount"), bool) or no_response.get("maxNoResponseCount", 0) <= 0:
        add_issue(issues, "flow.noResponse.maxNoResponseCount", "Must be a positive integer")
    hangup = no_response.get("noResponseHangUp")
    if not isinstance(hangup, dict):
        add_issue(issues, "flow.noResponse.noResponseHangUp", "Must be an object")
    else:
        if not isinstance(hangup.get("enabled"), bool):
            add_issue(issues, "flow.noResponse.noResponseHangUp.enabled", "Must be boolean")
        if not isinstance(hangup.get("hangupMessage"), str):
            add_issue(issues, "flow.noResponse.noResponseHangUp.hangupMessage", "Must be a string")
    if not isinstance(voicemail.get("enabled"), bool):
        add_issue(issues, "flow.voicemailNode.enabled", "Must be boolean")
    if voicemail.get("action") not in {"playThenHangup", "hangup"}:
        add_issue(issues, "flow.voicemailNode.action", "Unsupported voicemail action")
    if not isinstance(voicemail.get("content"), str):
        add_issue(issues, "flow.voicemailNode.content", "Must be a string")


def _validate_common(config, issues, standard: bool) -> None:
    validate_object_fields(config, "prompt", PROMPT_FIELDS, issues)
    validate_object_fields(config, "call_policy", CALL_POLICY_FIELDS, issues)
    policy = config.get("call_policy") if isinstance(config.get("call_policy"), dict) else {}
    duration = policy.get("max_call_duration_sec")
    if isinstance(duration, int) and not isinstance(duration, bool) and duration <= 0:
        add_issue(issues, "call_policy.max_call_duration_sec", "Must be positive")
    _validate_flow(config, issues, standard)


def _validate_standard(config, catalog, issues, warnings) -> None:
    if "realtime" in config:
        add_issue(issues, "realtime", "Standard config cannot contain realtime")
    _validate_common(config, issues, standard=True)
    validate_object_fields(config, "asr", {
        "provider": "string", "model": "string", "language": "string",
        "sample_rate": "integer", "encoding": "string", "interim_results": "boolean",
        "endpointing_ms": "integer", "provider_config": "object",
    }, issues, optional_fields={"provider_config"})
    validate_object_fields(config, "llm", STANDARD_LLM_FIELDS, issues)
    validate_object_fields(config, "tts", {
        "provider": "string", "model": "string", "voice_id": "string", "language": "string",
        "speed": "number", "volume": "number", "sample_rate": "integer", "encoding": "string",
        "provider_config": "object",
    }, issues, optional_fields={"provider_config"})

    asr = config.get("asr") if isinstance(config.get("asr"), dict) else {}
    llm = config.get("llm") if isinstance(config.get("llm"), dict) else {}
    tts = config.get("tts") if isinstance(config.get("tts"), dict) else {}
    asr_provider = catalog["asr"].get(asr.get("provider"))
    if asr_provider is None or not asr_provider.get("enabled", False):
        add_issue(issues, "asr.provider", "Unsupported or disabled ASR provider")
    else:
        asr_model = asr_provider["models"].get(asr.get("model"))
        if asr_model is None or not asr_model.get("enabled", False):
            add_issue(issues, "asr.model", "Unsupported or disabled ASR model")
        else:
            if asr.get("language") not in asr_model["languages"]:
                add_issue(issues, "asr.language", "ASR model does not support this language", "ASR_LANGUAGE_UNSUPPORTED")
            if asr_model.get("deprecated"):
                warnings.append({"code": "DEPRECATED_CAPABILITY", "path": "asr.model", "message": "ASR model is deprecated"})
        if asr.get("sample_rate") not in asr_provider["sample_rates"]:
            add_issue(issues, "asr.sample_rate", "Unsupported ASR sample rate")
        if asr.get("encoding") not in asr_provider["encodings"]:
            add_issue(issues, "asr.encoding", "Unsupported ASR encoding")

    tts_provider = catalog["tts"].get(tts.get("provider"))
    if tts_provider is None or not tts_provider.get("enabled", False):
        add_issue(issues, "tts.provider", "Unsupported or disabled TTS provider")
    else:
        tts_model = tts_provider["models"].get(tts.get("model"))
        if tts_model is None or not tts_model.get("enabled", False):
            add_issue(issues, "tts.model", "Unsupported or disabled TTS model")
        voice_input = tts_provider.get("voice_input", "catalog")
        voice = next(
            (item for item in tts_provider["voices"] if item["voice_id"] == tts.get("voice_id")),
            None,
        )
        if voice_input == "custom" or (voice_input == "catalog_or_custom" and voice is None):
            if not str(tts.get("voice_id") or "").strip():
                add_issue(issues, "tts.voice_id", "A custom TTS voice is required", "TTS_VOICE_UNSUPPORTED")
            elif tts_model and tts.get("language") not in tts_model.get("languages", []):
                add_issue(issues, "tts.language", "TTS model does not support this language", "TTS_VOICE_LANGUAGE_MISMATCH")
        else:
            if voice is None or not voice.get("enabled", False):
                add_issue(issues, "tts.voice_id", "Unsupported or disabled TTS voice", "TTS_VOICE_UNSUPPORTED")
            else:
                if tts.get("model") not in voice["models"]:
                    add_issue(issues, "tts.voice_id", "Voice does not support this model", "TTS_VOICE_UNSUPPORTED")
                if tts.get("language") not in voice["languages"]:
                    add_issue(issues, "tts.language", "Voice does not support this language", "TTS_VOICE_LANGUAGE_MISMATCH")
                if voice.get("deprecated"):
                    warnings.append({"code": "DEPRECATED_CAPABILITY", "path": "tts.voice_id", "message": "TTS voice is deprecated"})
        speed_range = tts_provider.get("speed_range", {"min": 0.5, "max": 2})
        speed = tts.get("speed")
        if is_number(speed) and not speed_range["min"] <= speed <= speed_range["max"]:
            add_issue(issues, "tts.speed", f"Must be between {speed_range['min']} and {speed_range['max']}")
        if tts.get("sample_rate") not in tts_provider["sample_rates"]:
            add_issue(issues, "tts.sample_rate", "Unsupported TTS sample rate")
        if tts.get("encoding") not in tts_provider["encodings"]:
            add_issue(issues, "tts.encoding", "Unsupported TTS encoding")

    for block, field, minimum, maximum in (
        (llm, "temperature", 0, 2),
        (llm, "top_p", 0, 1),
        (tts, "volume", 0, 100),
    ):
        value = block.get(field)
        if is_number(value) and not minimum <= value <= maximum:
            add_issue(issues, f"{block}.{field}", f"Must be between {minimum} and {maximum}")
    for block, field in (
        (asr, "endpointing_ms"),
        (llm, "context_rounds"),
        (llm, "max_tokens"),
        (llm, "timeout_ms"),
    ):
        value = block.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value <= 0:
            add_issue(issues, f"{block}.{field}", "Must be positive")


def _validate_realtime(config, catalog, issues) -> None:
    for forbidden in ("asr", "tts"):
        if forbidden in config:
            add_issue(issues, forbidden, "Realtime config cannot contain Standard blocks")
    _validate_common(config, issues, standard=False)
    validate_object_fields(config, "llm", {"provider": "string", "model": "string"}, issues)
    validate_object_fields(config, "realtime", {
        "transcription_model": "string", "language": "string", "voice_id": "string",
        "noise_reduction": "boolean", "input_audio_format": "string",
        "output_audio_format": "string", "turn_detection": "object",
    }, issues)
    realtime = config.get("realtime") if isinstance(config.get("realtime"), dict) else {}
    voice = next(
        (item for item in catalog["realtime"]["voices"] if item["voice_id"] == realtime.get("voice_id")),
        None,
    )
    if voice is None or not voice.get("enabled", False):
        add_issue(issues, "realtime.voice_id", "Unsupported or disabled Realtime voice", "TTS_VOICE_UNSUPPORTED")
    elif realtime.get("language") not in voice["languages"]:
        add_issue(issues, "realtime.language", "Voice does not support this language", "TTS_VOICE_LANGUAGE_MISMATCH")
    if realtime.get("transcription_model") not in catalog["realtime"]["transcription_models"]:
        add_issue(issues, "realtime.transcription_model", "Unsupported transcription model")
    for field in ("input_audio_format", "output_audio_format"):
        if realtime.get(field) not in catalog["realtime"][f"{field}s"]:
            add_issue(issues, f"realtime.{field}", "Unsupported audio format")


def validate_app_config(config, catalog, runtime_resolver) -> ValidationReport:
    issues = []
    warnings = []
    catalog_version = catalog.version
    if not isinstance(config, dict):
        add_issue(issues, "", "Configuration must be an object")
        return ValidationReport(False, issues, warnings, catalog_version)

    validate_base_contract(config, issues)
    llm = config.get("llm") if isinstance(config.get("llm"), dict) else {}
    runtime = None
    try:
        if not isinstance(llm.get("provider"), str) or not isinstance(llm.get("model"), str):
            raise RobotConfigError(
                "UNSUPPORTED_MODEL_RUNTIME",
                "llm.provider and llm.model must be strings",
            )
        runtime = runtime_resolver(llm["provider"], llm["model"], catalog)
        if not runtime.remote_supported:
            add_issue(issues, "llm.model", "Remote runtime adapter is not enabled", "RUNTIME_ADAPTER_UNAVAILABLE")
        if runtime.deprecated:
            warnings.append({"code": "DEPRECATED_CAPABILITY", "path": "llm.model", "message": "Runtime profile is deprecated"})
    except RobotConfigError as exc:
        add_issue(issues, "llm.model", exc.message, exc.code)

    if runtime is not None:
        for block in catalog.data["required_blocks"].get(runtime.engine, []):
            if block not in config:
                add_issue(issues, block, "Required configuration block is missing")
        if runtime.engine == "standard":
            _validate_standard(config, catalog.data, issues, warnings)
        elif runtime.engine == "realtime":
            _validate_realtime(config, catalog.data, issues)
        else:
            add_issue(issues, "llm.model", "Unsupported runtime engine", "UNSUPPORTED_RUNTIME_ENGINE")

    variable_keys = validate_variables(config, issues)
    validate_variable_references(config, variable_keys, issues)
    validate_no_secrets(config, issues)
    config_hash = calculate_config_hash(config, runtime) if not issues and runtime else None
    return ValidationReport(
        valid=not issues,
        errors=issues,
        warnings=warnings,
        catalog_version=catalog_version,
        runtime_resolution=runtime,
        config_hash=config_hash,
    )
