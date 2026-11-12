import os
import unittest
from unittest.mock import patch

from pipecat.services.elevenlabs.tts import ElevenLabsTTSService as PipecatElevenLabsTTSService
from pipecat.transcriptions.language import Language

from collection_agents.bots.copilot.config import CopilotConfigManager
from collection_agents.bots.copilot.services import create_tts_service as create_copilot_tts_service
from collection_agents.services.call_config import CallConfigManager
from collection_agents.services.providers import TTS_SERVICE_BUILDERS, create_tts_service


class TTSConfigTests(unittest.TestCase):
    def test_standard_elevenlabs_uses_provider_defaults(self):
        manager = CallConfigManager(
            {
                "data": {
                    "config": {
                        "tts": {
                            "provider": " ElevenLabs ",
                            "voice_id": "voice-1",
                            "language": "en-US",
                        }
                    }
                }
            }
        )

        config = manager.get_tts_config()

        self.assertEqual(config["provider"], "elevenlabs")
        self.assertEqual(config["model"], "eleven_flash_v2_5")
        self.assertEqual(config["voice_id"], "voice-1")
        self.assertEqual(config["locale"], "en-US")

    def test_copilot_elevenlabs_uses_provider_defaults(self):
        manager = CopilotConfigManager(
            {
                "config": {
                    "tts": {
                        "provider": "elevenlabs",
                        "name": "voice-1",
                        "locale": "es-MX",
                    }
                }
            }
        )

        config = manager.get_tts_config()

        self.assertEqual(config["provider"], "elevenlabs")
        self.assertEqual(config["model"], "eleven_flash_v2_5")
        self.assertEqual(config["voice_id"], "voice-1")
        self.assertEqual(config["locale"], "es-MX")

    def test_default_tts_provider_remains_cartesia(self):
        standard = CallConfigManager({}).get_tts_config()
        copilot = CopilotConfigManager({}).get_tts_config()

        self.assertEqual(standard["provider"], "cartesia")
        self.assertEqual(standard["model"], "sonic-3.6")
        self.assertEqual(copilot["provider"], "cartesia")
        self.assertEqual(copilot["model"], "sonic-3.6")

    def test_remote_values_override_local_fallbacks(self):
        payload = {
            "tts": {
                "provider": "elevenlabs",
                "voice_id": "remote-voice",
                "model": "remote-model",
                "language": "en-US",
            }
        }
        configs = [
            CallConfigManager({"config": payload}).get_tts_config(),
            CopilotConfigManager({"config": payload}).get_tts_config(),
        ]

        for config in configs:
            self.assertEqual(config["provider"], "elevenlabs")
            self.assertEqual(config["voice_id"], "remote-voice")
            self.assertEqual(config["model"], "remote-model")


class ElevenLabsTTSFactoryTests(unittest.TestCase):
    @patch("collection_agents.services.providers.ElevenLabsTTSService")
    def test_builds_official_service_with_telephony_config(self, factory):
        factory.InputParams = PipecatElevenLabsTTSService.InputParams
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}, clear=True):
            service = create_tts_service(
                {
                    "provider": " ElevenLabs ",
                    "voice_id": " voice-1 ",
                    "model": "eleven_turbo_v2_5",
                    "locale": "es-MX",
                    "speed": 0.9,
                }
            )

        self.assertIs(service, factory.return_value)
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["voice_id"], "voice-1")
        self.assertEqual(kwargs["model"], "eleven_turbo_v2_5")
        self.assertEqual(kwargs["sample_rate"], 8000)
        self.assertEqual(kwargs["params"].language, Language.ES)
        self.assertEqual(kwargs["params"].speed, 0.9)

    @patch("collection_agents.services.providers.ElevenLabsTTSService")
    def test_uses_environment_voice_and_default_model(self, factory):
        with patch.dict(
            os.environ,
            {
                "ELEVENLABS_API_KEY": "test-key",
                "ELEVENLABS_VOICE_ID": "env-voice",
            },
            clear=True,
        ):
            create_tts_service({"provider": "elevenlabs"})

        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["voice_id"], "env-voice")
        self.assertEqual(kwargs["model"], "eleven_flash_v2_5")

    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "ELEVENLABS_API_KEY"):
                create_tts_service(
                    {"provider": "elevenlabs", "voice_id": "voice-1"}
                )

    def test_requires_voice_id(self):
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}, clear=True):
            with self.assertRaisesRegex(ValueError, "voice_id"):
                create_tts_service({"provider": "elevenlabs"})

    def test_rejects_unsupported_speed(self):
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}, clear=True):
            with self.assertRaisesRegex(ValueError, "between 0.7 and 1.2"):
                create_tts_service(
                    {
                        "provider": "elevenlabs",
                        "voice_id": "voice-1",
                        "speed": 1.5,
                    }
                )


class CopilotTTSFactoryTests(unittest.TestCase):
    @patch("collection_agents.bots.copilot.services.create_provider_tts_service")
    def test_delegates_elevenlabs_to_shared_factory(self, factory):
        config = {
            "provider": "elevenlabs",
            "voice_id": "voice-1",
            "model": "eleven_flash_v2_5",
        }

        service = create_copilot_tts_service(config)

        self.assertIs(service, factory.return_value)
        factory.assert_called_once_with(config)

    @patch("collection_agents.bots.copilot.services.create_provider_tts_service")
    def test_preserves_cartesia_compatibility_defaults(self, factory):
        create_copilot_tts_service(
            {"provider": "cartesia", "model": "legacy-model", "voice_id": ""}
        )

        config = factory.call_args.args[0]
        self.assertEqual(config["model"], "sonic-multilingual")
        self.assertEqual(
            config["voice_id"], "e50405f9-347e-4a1d-b5b6-943bbdb30cd5"
        )


class ExtensibleTTSProviderTests(unittest.TestCase):
    def test_provider_specific_config_survives_both_config_managers(self):
        provider_config = {
            "endpoint": "wss://speech.example.cn",
            "resource_id": "resource-1",
        }
        payload = {
            "tts": {
                "provider": "domestic",
                "provider_config": provider_config,
            }
        }
        standard = CallConfigManager({"config": payload}).get_tts_config()
        copilot = CopilotConfigManager({"config": payload}).get_tts_config()

        self.assertEqual(standard["provider_config"], provider_config)
        self.assertEqual(copilot["provider_config"], provider_config)

    def test_additional_provider_uses_registered_builder(self):
        expected = object()
        calls = []

        def builder(config):
            calls.append(config)
            return expected

        config = {
            "provider": "domestic",
            "provider_config": {"resource_id": "resource-1"},
        }
        with patch.dict(TTS_SERVICE_BUILDERS, {"domestic": builder}):
            service = create_tts_service(config)

        self.assertIs(service, expected)
        self.assertEqual(calls, [config])

    @patch("collection_agents.bots.copilot.services.create_provider_tts_service")
    def test_copilot_accepts_additional_registered_provider(self, factory):
        def builder(config):
            return factory.return_value

        with patch.dict(TTS_SERVICE_BUILDERS, {"domestic": builder}):
            service = create_copilot_tts_service(
                {
                    "provider": "domestic",
                    "provider_config": {"resource_id": "resource-1"},
                }
            )

        self.assertIs(service, factory.return_value)
        config = factory.call_args.args[0]
        self.assertEqual(config["provider"], "domestic")
        self.assertEqual(
            config["provider_config"], {"resource_id": "resource-1"}
        )


if __name__ == "__main__":
    unittest.main()
