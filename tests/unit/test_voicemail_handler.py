import unittest
from unittest.mock import patch

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMMessagesAppendFrame,
    OutputAudioRawFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from collection_agents.events import (
    VoicemailDetectedEvent,
    VoicemailDetectedFrame,
)
from collection_agents.handlers.standard.voicemail import StandardVoicemailHandler
from collection_agents.services.voicemail_audio_repository import (
    VoicemailAudioRepository,
)
from collection_agents.services.voicemail_player import FixedVoicemailPlayer


class RecordingHandler(StandardVoicemailHandler):
    def __init__(self, *args, **kwargs):
        self.pushed = []
        super().__init__(*args, **kwargs)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append(frame)


class VoicemailHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_message_splits_internally_and_completes(self):
        detected = []
        finalized = []
        completed = []
        handler = RecordingHandler(
            mode="fixed_message",
            on_detected=lambda: detected.append(True),
            finalize_stt_cb=lambda: finalized.append(True),
            fixed_text="First sentence. Payment reminder",
            fixed_done_cb=lambda: completed.append(True),
        )

        await handler.process_frame(
            VoicemailDetectedFrame(
                event=VoicemailDetectedEvent(transcript_text="voicemail")
            ),
            FrameDirection.DOWNSTREAM,
        )
        self.assertEqual(detected, [True])
        self.assertEqual(finalized, [True])
        self.assertIsInstance(handler.pushed[0], LLMMessagesAppendFrame)
        self.assertFalse(handler.pushed[0].run_llm)
        self.assertEqual(
            handler.pushed[0].messages[0]["content"],
            "First sentence. Payment reminder",
        )
        self.assertEqual(handler.pushed[1].text, "First sentence.")

        await handler.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        speech = [frame for frame in handler.pushed if isinstance(frame, TTSSpeakFrame)]
        self.assertEqual(speech[-1].text, "Payment reminder")
        self.assertEqual(completed, [])

        await handler.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        self.assertEqual(completed, [True])

    async def test_detection_while_bot_speaking_is_deferred(self):
        handler = RecordingHandler(mode="fixed_message", fixed_text="Reminder")
        await handler.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await handler.process_frame(VoicemailDetectedFrame(), FrameDirection.DOWNSTREAM)
        self.assertFalse(any(isinstance(frame, LLMMessagesAppendFrame) for frame in handler.pushed))

        await handler.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        self.assertTrue(any(isinstance(frame, LLMMessagesAppendFrame) for frame in handler.pushed))

    async def test_legacy_mode_builds_prompt_in_handler(self):
        handler = RecordingHandler(mode="legacy_llm", locale="en-US")
        await handler.process_frame(
            VoicemailDetectedFrame(
                event=VoicemailDetectedEvent(
                    transcript_text="Please leave a message"
                )
            ),
            FrameDirection.DOWNSTREAM,
        )
        self.assertEqual(len(handler.pushed), 1)
        self.assertIsInstance(handler.pushed[0], LLMMessagesAppendFrame)
        self.assertTrue(handler.pushed[0].run_llm)
        prompt = handler.pushed[0].messages[0]["content"]
        self.assertIn("Trigger flow : Voicemail detected", prompt)
        self.assertIn("hang up the call: Goodbye", prompt)

    async def test_hangup_mode_calls_instant_close(self):
        closed = []
        handler = RecordingHandler(
            mode="hangup",
            instant_close_cb=lambda: closed.append(True),
        )
        await handler.process_frame(VoicemailDetectedFrame(), FrameDirection.DOWNSTREAM)
        self.assertEqual(closed, [True])
        self.assertEqual(handler.pushed, [])

    async def test_cached_audio_is_used_for_remainder(self):
        audio = OutputAudioRawFrame(
            audio=b"\x00\x00" * 160,
            sample_rate=8000,
            num_channels=1,
        )
        completed = []
        player = FixedVoicemailPlayer(
            text="First sentence. Cached reminder",
            audio_lookup=lambda: audio,
            on_complete=lambda: completed.append(True),
        )
        emitted = []

        async def emit(frame, direction):
            emitted.append(frame)

        await player.play(emit, FrameDirection.DOWNSTREAM)
        await player.on_bot_stopped(emit, FrameDirection.DOWNSTREAM)
        self.assertIn(audio, emitted)
        await player.on_bot_stopped(emit, FrameDirection.DOWNSTREAM)
        self.assertEqual(completed, [True])
        await player.cleanup()

    def test_audio_repository_returns_matching_cached_audio(self):
        audio = OutputAudioRawFrame(
            audio=b"\x00\x00" * 160,
            sample_rate=8000,
            num_channels=1,
        )
        asset = {
            "bot_code": "robot-1",
            "provider": "cartesia",
            "voice_id": "voice-1",
            "model": "sonic-2",
            "locale": "en-US",
            "sample_rate": 8000,
            "audio_format": "wav",
            "text_type": "voicemail_message",
            "text": "Cached reminder",
            "text_hash": "hash-1",
            "file_path": "cache.wav",
        }
        playback = []
        repository = VoicemailAudioRepository(
            robot_code="robot-1",
            tts_config={
                "provider": "cartesia",
                "voice_id": "voice-1",
                "model": "sonic-2",
                "locale": "en-US",
            },
            text="First sentence. Cached reminder",
            cache_enabled=True,
            on_playback=playback.append,
        )
        with patch(
            "collection_agents.services.voicemail_audio_repository.find_audio_asset",
            return_value=asset,
        ), patch(
            "collection_agents.services.voicemail_audio_repository.load_audio_frame",
            return_value=audio,
        ):
            result = repository.find_cached_audio()
        self.assertEqual(result["frame"], audio)
        self.assertEqual(playback[0]["source"], "local_cache")


if __name__ == "__main__":
    unittest.main()
