import os
import unittest
from unittest.mock import patch

from collection_agents.bots.realtime import RealtimeResourceManager
from collection_agents.infra.paths import DATA_DIR
from collection_agents.services.audio_assets import REGISTRY_PATH
from collection_agents.services.providers import (
    create_stt_service,
    resolve_bot_llm_runtime,
    resolve_stt_provider,
)


class ConfigSourceTests(unittest.TestCase):
    def test_stt_provider_comes_from_delivered_config(self):
        self.assertEqual(
            resolve_stt_provider({"provider": "vosk"}, "any-robot"),
            "vosk",
        )
        self.assertEqual(resolve_stt_provider({}, "any-robot"), "deepgram")

    def test_llm_provider_is_not_overridden_by_robot_code(self):
        delivered = {"provider": "deepseek", "model": "deepseek-chat"}
        resolved, platform = resolve_bot_llm_runtime(delivered, "bot", "any-robot")
        self.assertEqual(resolved, delivered)
        self.assertEqual(platform, "CPU")

    def test_deepgram_model_comes_from_delivered_config(self):
        with patch.dict(os.environ, {"DEEPGRAM_API_KEY": "test-key"}):
            with patch("collection_agents.services.providers.DeepgramSTTService") as factory:
                create_stt_service(
                    {"provider": "deepgram", "model": "nova-3", "locale": "es-MX"},
                    "any-robot",
                    "bot",
                )
        self.assertEqual(factory.call_args.kwargs["live_options"].model, "nova-3")

    def test_realtime_resource_comes_from_environment(self):
        env = {
            "AZURE_REALTIME_ENDPOINT": "wss://example.openai.azure.com",
            "AZURE_REALTIME_MODEL": "gpt-realtime-mini",
            "AZURE_REALTIME_TRANSCRIPTION_MODEL": "gpt-4o-mini-transcribe",
            "AZURE_REALTIME_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env):
            manager = RealtimeResourceManager()
        self.assertEqual(manager._resource["model"], "gpt-realtime-mini")
        self.assertEqual(
            manager._resource["asr_model"],
            "gpt-4o-mini-transcribe",
        )

    def test_audio_registry_is_runtime_data(self):
        self.assertEqual(REGISTRY_PATH, DATA_DIR / "cartesia_audio_assets.json")


if __name__ == "__main__":
    unittest.main()
