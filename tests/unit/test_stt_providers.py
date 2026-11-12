import os
import unittest
from unittest.mock import patch

from pipecat.services.speechmatics.stt import SpeechmaticsSTTService as PipecatSpeechmaticsSTTService
from pipecat.transcriptions.language import Language

from collection_agents.bots.copilot.config import CopilotConfigManager
from collection_agents.bots.copilot.services import create_stt_service as create_copilot_stt_service
from collection_agents.services.call_config import CallConfigManager
from collection_agents.services.providers import STT_SERVICE_BUILDERS, create_stt_service


class ASRConfigTests(unittest.TestCase):
    def test_new_provider_defaults_and_params_reach_both_modes(self):
        expected_models = {
            "assemblyai": "u3-rt-pro",
            "speechmatics": "enhanced",
            "soniox": "stt-rt-v4",
        }
        for provider, expected_model in expected_models.items():
            with self.subTest(provider=provider):
                payload = {
                    "asr": {
                        "provider": provider,
                        "language": "en-US",
                        "provider_config": {
                            "params": {"custom_setting": provider},
                        },
                    }
                }
                standard = CallConfigManager({"config": payload}).get_asr_config()
                copilot = CopilotConfigManager({"config": payload}).get_asr_config()

                for config in (standard, copilot):
                    self.assertEqual(config["provider"], provider)
                    self.assertEqual(config["model"], expected_model)
                    self.assertEqual(config["locale"], "en-US")
                    self.assertEqual(
                        config["provider_config"],
                        {"params": {"custom_setting": provider}},
                    )

    def test_remote_model_overrides_local_fallback(self):
        payload = {
            "asr": {
                "provider": "assemblyai",
                "model": "remote-model",
                "language": "en-US",
            }
        }
        configs = [
            CallConfigManager({"config": payload}).get_asr_config(),
            CopilotConfigManager({"config": payload}).get_asr_config(),
        ]

        for config in configs:
            self.assertEqual(config["provider"], "assemblyai")
            self.assertEqual(config["model"], "remote-model")


class AssemblyAISTTFactoryTests(unittest.TestCase):
    @patch("collection_agents.services.providers.AssemblyAISTTService")
    def test_builds_official_service_with_telephony_config(self, factory):
        with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}, clear=True):
            service = create_stt_service(
                {
                    "provider": "assemblyai",
                    "locale": "en-US",
                    "model": "u3-rt-pro",
                    "provider_config": {
                        "params": {
                            "keyterms_prompt": ["payment"],
                            "min_turn_silence": 200,
                        },
                        "vad_force_turn_endpoint": False,
                    },
                }
            )

        self.assertIs(service, factory.return_value)
        kwargs = factory.call_args.kwargs
        connection = kwargs["connection_params"]
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["language"], Language("en-US"))
        self.assertFalse(kwargs["vad_force_turn_endpoint"])
        self.assertEqual(connection.sample_rate, 8000)
        self.assertEqual(connection.encoding, "pcm_s16le")
        self.assertEqual(connection.speech_model, "u3-rt-pro")
        self.assertEqual(connection.keyterms_prompt, ["payment"])
        self.assertEqual(connection.min_turn_silence, 200)


class SpeechmaticsSTTFactoryTests(unittest.TestCase):
    @patch("collection_agents.services.providers.SpeechmaticsSTTService")
    def test_builds_official_service_with_telephony_config(self, factory):
        factory.InputParams = PipecatSpeechmaticsSTTService.InputParams
        with patch.dict(os.environ, {"SPEECHMATICS_API_KEY": "test-key"}, clear=True):
            service = create_stt_service(
                {
                    "provider": "speechmatics",
                    "locale": "en-US",
                    "model": "enhanced",
                    "provider_config": {
                        "base_url": "wss://us.rt.speechmatics.com/v2",
                        "params": {
                            "max_delay": 1.5,
                            "enable_diarization": True,
                        },
                    },
                }
            )

        self.assertIs(service, factory.return_value)
        kwargs = factory.call_args.kwargs
        params = kwargs["params"]
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["base_url"], "wss://us.rt.speechmatics.com/v2")
        self.assertEqual(kwargs["sample_rate"], 8000)
        self.assertEqual(params.language, Language("en-US"))
        self.assertEqual(
            params.operating_point,
            PipecatSpeechmaticsSTTService.OperatingPoint.ENHANCED,
        )
        self.assertEqual(
            params.audio_encoding,
            PipecatSpeechmaticsSTTService.AudioEncoding.PCM_S16LE,
        )
        self.assertEqual(params.max_delay, 1.5)
        self.assertTrue(params.enable_diarization)


class SonioxSTTFactoryTests(unittest.TestCase):
    @patch("collection_agents.services.providers.SonioxSTTService")
    def test_builds_official_service_with_telephony_config(self, factory):
        with patch.dict(os.environ, {"SONIOX_API_KEY": "test-key"}, clear=True):
            service = create_stt_service(
                {
                    "provider": "soniox",
                    "locale": "es-MX",
                    "provider_config": {
                        "url": "wss://stt-rt.soniox.com/transcribe-websocket",
                        "params": {"enable_language_identification": True},
                    },
                }
            )

        self.assertIs(service, factory.return_value)
        kwargs = factory.call_args.kwargs
        params = kwargs["params"]
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["sample_rate"], 8000)
        self.assertEqual(params.model, "stt-rt-v4")
        self.assertEqual(params.audio_format, "pcm_s16le")
        self.assertEqual(params.num_channels, 1)
        self.assertEqual(params.language_hints, [Language("es-MX")])
        self.assertTrue(params.enable_language_identification)


class STTFactoryContractTests(unittest.TestCase):
    def test_new_providers_require_their_api_keys(self):
        cases = {
            "assemblyai": "ASSEMBLYAI_API_KEY",
            "speechmatics": "SPEECHMATICS_API_KEY",
            "soniox": "SONIOX_API_KEY",
        }
        for provider, env_name in cases.items():
            with self.subTest(provider=provider):
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaisesRegex(ValueError, env_name):
                        create_stt_service({"provider": provider})

    def test_additional_provider_uses_registered_builder(self):
        expected = object()
        calls = []

        def builder(config, robot_code, robot_name, grammar):
            calls.append((config, robot_code, robot_name, grammar))
            return expected

        config = {"provider": "domestic", "provider_config": {"region": "cn"}}
        with patch.dict(STT_SERVICE_BUILDERS, {"domestic": builder}):
            service = create_stt_service(config, "robot-1", "Robot", ["yes"])

        self.assertIs(service, expected)
        self.assertEqual(calls, [(config, "robot-1", "Robot", ["yes"])])

    @patch("collection_agents.services.providers.DeepgramSTTService")
    def test_copilot_preserves_deepgram_smart_format(self, factory):
        create_copilot_stt_service({"provider": "deepgram", "locale": "en-US"})

        options = factory.call_args.kwargs["live_options"]
        self.assertTrue(options.smart_format)
        self.assertEqual(options.sample_rate, 8000)

    @patch("collection_agents.bots.copilot.services.create_provider_stt_service")
    def test_copilot_delegates_new_providers_to_shared_factory(self, factory):
        for provider in ("assemblyai", "speechmatics", "soniox"):
            with self.subTest(provider=provider):
                factory.reset_mock()
                config = {"provider": provider, "locale": "en-US"}
                service = create_copilot_stt_service(config)
                self.assertIs(service, factory.return_value)
                factory.assert_called_once_with(config)


if __name__ == "__main__":
    unittest.main()
