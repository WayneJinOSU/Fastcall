import copy
import unittest

from robot_config_service.capabilities.loader import CapabilityCatalog, load_catalog
from robot_config_service.capabilities.runtime_resolver import resolve_runtime
from robot_config_service.domain.hashing import calculate_config_hash
from robot_config_service.domain.validation import validate_app_config

from tests.unit.robot_config_service.fixtures import valid_config


class DomainValidationTests(unittest.TestCase):
    def validate(self, config, catalog=None):
        return validate_app_config(config, catalog or load_catalog(), resolve_runtime)

    def test_valid_standard_config(self):
        report = self.validate(valid_config())
        self.assertTrue(report.valid, report.as_dict())
        self.assertEqual(report.runtime_resolution.engine, "standard")
        self.assertEqual(report.runtime_resolution.adapter, "deepseek_chat")
        self.assertTrue(report.config_hash.startswith("sha256:"))

    def test_welcome_node_only_allows_content(self):
        config = valid_config()
        config["flow"]["welcomeNode"]["timeoutSec"] = 10
        config["flow"]["welcomeNode"]["timeoutReply"] = "Hello"
        report = self.validate(config)
        self.assertFalse(report.valid)
        self.assertIn("flow.welcomeNode", {issue.path for issue in report.errors})

    def test_runtime_requires_exact_provider_and_model(self):
        for model in ("deepseek-flash-preview", "gpt-realtime-mini", "DEEPSEEK-FLASH"):
            with self.subTest(model=model):
                config = valid_config()
                config["llm"]["model"] = model
                report = self.validate(config)
                self.assertFalse(report.valid)
                self.assertEqual(report.errors[0].code, "UNSUPPORTED_MODEL_RUNTIME")

    def test_disabled_runtime_cannot_publish_new_version(self):
        catalog = copy.deepcopy(load_catalog().data)
        catalog["runtime_profiles"][0]["enabled"] = False
        report = self.validate(valid_config(), CapabilityCatalog(catalog))
        self.assertFalse(report.valid)
        self.assertEqual(report.errors[0].code, "UNSUPPORTED_MODEL_RUNTIME")

    def test_language_voice_and_combination_validation(self):
        cases = (
            ("asr", "language", "fr-FR", "ASR_LANGUAGE_UNSUPPORTED"),
            ("tts", "voice_id", "missing", "TTS_VOICE_UNSUPPORTED"),
            ("tts", "language", "fr-FR", "TTS_VOICE_LANGUAGE_MISMATCH"),
        )
        for block, field, value, expected in cases:
            with self.subTest(expected=expected):
                config = valid_config()
                config[block][field] = value
                report = self.validate(config)
                self.assertIn(expected, {issue.code for issue in report.errors})

    def test_realtime_is_recognized_but_remote_publish_is_disabled(self):
        config = valid_config()
        config.pop("asr")
        config.pop("tts")
        config["llm"] = {"provider": "azure", "model": "gpt-realtime-mini"}
        config["realtime"] = {
            "transcription_model": "gpt-4o-mini-transcribe", "language": "en-US",
            "voice_id": "alloy", "noise_reduction": True,
            "input_audio_format": "pcm16", "output_audio_format": "pcm16",
            "turn_detection": {"type": "server_vad"},
        }
        report = self.validate(config)
        self.assertEqual(report.runtime_resolution.engine, "realtime")
        self.assertIn("RUNTIME_ADAPTER_UNAVAILABLE", {issue.code for issue in report.errors})

    def test_realtime_forbids_standard_blocks(self):
        config = valid_config()
        config["llm"] = {"provider": "azure", "model": "gpt-realtime-mini"}
        config["realtime"] = {
            "transcription_model": "gpt-4o-mini-transcribe", "language": "en-US",
            "voice_id": "alloy", "noise_reduction": True,
            "input_audio_format": "pcm16", "output_audio_format": "pcm16",
            "turn_detection": {"type": "server_vad"},
        }
        report = self.validate(config)
        paths = {issue.path for issue in report.errors}
        self.assertIn("asr", paths)
        self.assertIn("tts", paths)

    def test_undefined_variables_and_secrets_are_rejected(self):
        config = valid_config()
        config["prompt"]["system_template"] = "Hello {{missing}} sk-car-abcdefghijklmnop"
        report = self.validate(config)
        messages = [issue.message for issue in report.errors]
        self.assertTrue(any("Undefined variable" in message for message in messages))
        self.assertTrue(any("Suspected API key" in message for message in messages))

    def test_cartesia_catalog_has_named_regional_voices(self):
        cartesia = load_catalog().data["tts"]["cartesia"]
        voices = cartesia["voices"]
        self.assertGreaterEqual(len(voices), 30)
        self.assertEqual(len({voice["voice_id"] for voice in voices}), len(voices))
        for voice in voices:
            self.assertTrue(voice["name"].strip())
            self.assertTrue(voice["country"].strip())
            self.assertTrue(voice["models"])
            self.assertTrue(voice["languages"])
            for model in voice["models"]:
                self.assertIn(model, cartesia["models"])
        locales = {locale for voice in voices for locale in voice["languages"]}
        self.assertTrue({"es-MX", "en-US", "ur-PK", "tl-PH", "vi-VN", "hi-IN", "en-IN"} <= locales)
        self.assertIn("sonic-3.6", cartesia["models"])

    def test_hash_canonicalization_and_runtime_sensitivity(self):
        config = valid_config()
        runtime = resolve_runtime("deepseek", "deepseek-flash", load_catalog())
        digest = calculate_config_hash(config, runtime)
        reordered = copy.deepcopy(config)
        reordered["app"] = {"name": "Standard English", "code": "example-standard-en"}
        self.assertEqual(calculate_config_hash(reordered, runtime), digest)

        reordered["variables"].reverse()
        self.assertNotEqual(calculate_config_hash(reordered, runtime), digest)
        other_runtime = copy.deepcopy(runtime)
        object.__setattr__(other_runtime, "adapter", "other")
        self.assertNotEqual(calculate_config_hash(config, other_runtime), digest)

        other_catalog = CapabilityCatalog({**copy.deepcopy(load_catalog().data), "catalog_version": "other"})
        other = resolve_runtime("deepseek", "deepseek-flash", other_catalog)
        self.assertEqual(calculate_config_hash(config, other), digest)

    def test_app_cannot_contain_runtime_or_environment_fields(self):
        for field, value in (("runtime_engine", "standard"), ("enabled", True), ("environment", "test")):
            with self.subTest(field=field):
                config = valid_config()
                config["app"][field] = value
                self.assertFalse(self.validate(config).valid)


if __name__ == "__main__":
    unittest.main()
