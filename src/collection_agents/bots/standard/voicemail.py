import asyncio
import logging
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    LLMMessagesAppendFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from collection_agents.processors.common import VoicemailDetectedFrame
from collection_agents.services.audio_assets import (
    DEFAULT_CARTESIA_VOICE_ID,
    DEFAULT_LOCALE,
    DEFAULT_MODEL,
    SAMPLE_RATE as AUDIO_ASSET_SAMPLE_RATE,
    TEXT_TYPE_VOICEMAIL,
    find_audio_asset,
    load_audio_frame,
    normalize_text as normalize_audio_asset_text,
    split_voicemail_segments,
)


class FixedVoicemailPlayer:
    def __init__(
        self,
        *,
        text,
        audio_lookup=None,
        on_complete=None,
        audio_done_padding=0.3,
    ):
        self._text = (text or "").strip()
        self._live_text, self._cached_text = split_voicemail_segments(self._text)
        self._audio_lookup = audio_lookup
        self._on_complete = on_complete
        self._audio_done_padding = audio_done_padding
        self._awaiting_live_stop = False
        self._awaiting_cached_stop = False
        self._done_task = None

    async def _call_on_complete(self):
        if self._on_complete:
            result = self._on_complete()
            if asyncio.iscoroutine(result):
                await result

    async def _lookup_audio(self):
        if not self._audio_lookup:
            return None
        try:
            result = self._audio_lookup()
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:
            logging.warning("[FixedVoicemailPlayer] Audio lookup failed: %s", exc)
            return None

        if isinstance(result, OutputAudioRawFrame):
            return result
        if not isinstance(result, dict):
            return None
        frame = result.get("frame") or result.get("audio_frame")
        return frame if isinstance(frame, OutputAudioRawFrame) else None

    @staticmethod
    def _audio_duration_secs(frame):
        sample_rate = getattr(frame, "sample_rate", None) or 0
        num_frames = getattr(frame, "num_frames", None) or 0
        return num_frames / sample_rate if sample_rate > 0 and num_frames > 0 else 0.0

    def _cancel_done_fallback(self):
        if self._done_task and not self._done_task.done():
            self._done_task.cancel()
        self._done_task = None

    async def cleanup(self):
        self._cancel_done_fallback()

    def _schedule_done_fallback(self, frame):
        self._cancel_done_fallback()
        delay = self._audio_duration_secs(frame) + self._audio_done_padding
        self._done_task = asyncio.create_task(self._done_fallback(delay))

    async def _done_fallback(self, delay):
        try:
            await asyncio.sleep(max(delay, 0.0))
            if not self._awaiting_cached_stop:
                return
            self._awaiting_cached_stop = False
            await self._call_on_complete()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.warning("[FixedVoicemailPlayer] Audio completion fallback failed: %s", exc)

    async def play(self, emit, direction):
        await emit(
            LLMMessagesAppendFrame(
                messages=[{"role": "assistant", "content": self._text}],
                run_llm=False,
            ),
            direction,
        )
        if self._live_text:
            self._awaiting_live_stop = True
            await emit(
                TTSSpeakFrame(self._live_text, append_to_context=False),
                direction,
            )
            return
        await self._play_cached(emit, direction)

    async def _play_cached(self, emit, direction):
        if not self._cached_text:
            await self._call_on_complete()
            return

        audio_frame = await self._lookup_audio()
        self._awaiting_cached_stop = True
        if audio_frame:
            await emit(audio_frame, direction)
            self._schedule_done_fallback(audio_frame)
            return
        await emit(
            TTSSpeakFrame(self._cached_text, append_to_context=False),
            direction,
        )

    async def on_bot_stopped(self, emit, direction) -> bool:
        if self._awaiting_live_stop:
            self._awaiting_live_stop = False
            await self._play_cached(emit, direction)
            return True
        if self._awaiting_cached_stop:
            self._cancel_done_fallback()
            self._awaiting_cached_stop = False
            await self._call_on_complete()
            return True
        return False


class StandardVoicemailHandler(FrameProcessor):
    def __init__(
        self,
        *,
        mode,
        on_detected=None,
        finalize_stt_cb=None,
        instant_close_cb=None,
        fixed_text=None,
        fixed_audio_lookup=None,
        fixed_done_cb=None,
    ):
        super().__init__()
        self._mode = mode
        self._on_detected = on_detected
        self._finalize_stt_cb = finalize_stt_cb
        self._instant_close_cb = instant_close_cb
        self._player = (
            FixedVoicemailPlayer(
                text=fixed_text,
                audio_lookup=fixed_audio_lookup,
                on_complete=fixed_done_cb,
            )
            if fixed_text
            else None
        )
        self._bot_speaking = False
        self._pending = False
        self._detected = False
        self._finalize_requested_at = None

    @staticmethod
    async def _call(callback):
        if callback:
            result = callback()
            if asyncio.iscoroutine(result):
                await result

    async def _dispatch(self, frame, direction):
        if self._player:
            await self._player.play(self.push_frame, direction)
            return
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "system", "content": frame.prompt}],
                run_llm=True,
            ),
            direction,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._pending:
                self._pending = False
                await self._dispatch(self._pending, FrameDirection.DOWNSTREAM)
            elif self._player:
                await self._player.on_bot_stopped(self.push_frame, FrameDirection.DOWNSTREAM)
        elif isinstance(frame, VoicemailDetectedFrame) and not self._detected:
            self._detected = True
            await self._call(self._on_detected)
            if self._finalize_stt_cb:
                try:
                    await self._call(self._finalize_stt_cb)
                    self._finalize_requested_at = time.monotonic()
                except Exception as exc:
                    logging.warning("[VoicemailHandler] STT finalize failed: %s", exc)
            if self._mode == "hangup":
                if self._instant_close_cb:
                    await self._call(self._instant_close_cb)
                else:
                    await self.push_frame(EndFrame(), direction)
                return
            if self._bot_speaking:
                self._pending = frame
                return
            await self._dispatch(frame, direction)
            return
        elif isinstance(frame, TranscriptionFrame) and self._finalize_requested_at is not None:
            self._finalize_requested_at = None

        if isinstance(frame, (EndFrame, CancelFrame)) and self._player:
            await self._player.cleanup()
        await self.push_frame(frame, direction)

    async def cleanup(self):
        await super().cleanup()
        if self._player:
            await self._player.cleanup()


class VoicemailAudioRepository:
    def __init__(
        self,
        *,
        robot_code,
        tts_config,
        text,
        cache_enabled,
        on_playback,
    ):
        self._robot_code = robot_code
        self._tts_config = tts_config
        self._text = text or ""
        self._cache_enabled = cache_enabled
        self._on_playback = on_playback

    def _remote_config(self):
        live_text, cached_text = split_voicemail_segments(self._text)
        return {
            "bot_code": self._robot_code,
            "provider": (self._tts_config.get("provider") or "cartesia").strip().lower(),
            "voice_id": self._tts_config.get("voice_id") or DEFAULT_CARTESIA_VOICE_ID,
            "model": self._tts_config.get("model") or DEFAULT_MODEL,
            "locale": self._tts_config.get("locale") or DEFAULT_LOCALE,
            "sample_rate": AUDIO_ASSET_SAMPLE_RATE,
            "audio_format": "wav",
            "text_type": TEXT_TYPE_VOICEMAIL,
            "live_text": live_text,
            "text": cached_text,
            "full_text": self._text,
        }

    @staticmethod
    def _local_config(asset):
        if not asset:
            return None
        return {
            key: asset.get(key)
            for key in (
                "bot_code", "provider", "voice_id", "model", "locale",
                "sample_rate", "audio_format", "text_type", "text_hash",
                "text", "file_path", "status", "enabled", "fallback",
            )
        }

    def _mark_playback(self, source, remote, **kwargs):
        playback = {"source": source, "remote": remote, **kwargs}
        self._on_playback(playback)
        return playback

    def find_cached_audio(self):
        remote = self._remote_config()
        if not self._cache_enabled:
            self._mark_playback("live_tts", remote, reason="cache_segment_missing")
            return None

        asset = find_audio_asset(self._robot_code)
        if not asset:
            self._mark_playback("live_tts", remote, reason="cache_miss")
            logging.info(
                "audio_asset_miss bot_code=%s reason=cache_miss",
                self._robot_code,
            )
            return None

        expected = {
            "provider": remote["provider"],
            "voice_id": remote["voice_id"],
            "model": remote["model"],
            "locale": remote["locale"],
            "sample_rate": remote["sample_rate"],
            "audio_format": remote["audio_format"],
            "text_type": remote["text_type"],
            "text": normalize_audio_asset_text(remote["text"]),
        }
        mismatches = []
        for field, expected_value in expected.items():
            actual_value = (
                normalize_audio_asset_text(asset.get(field, ""))
                if field == "text"
                else asset.get(field)
            )
            if actual_value != expected_value:
                mismatches.append(
                    f"{field}: expected={expected_value!r} actual={actual_value!r}"
                )

        local = self._local_config(asset)
        if mismatches:
            self._mark_playback(
                "live_tts",
                remote,
                reason="cache_mismatch",
                mismatches=mismatches,
                local=local,
            )
            logging.info(
                "audio_asset_miss bot_code=%s reason=cache_mismatch mismatches=%s",
                self._robot_code,
                mismatches,
            )
            return None

        try:
            frame = load_audio_frame(asset)
        except Exception as exc:
            self._mark_playback(
                "live_tts",
                remote,
                reason="cache_load_failed",
                local=local,
            )
            logging.warning(
                "audio_asset_miss bot_code=%s reason=cache_load_failed error=%s",
                self._robot_code,
                exc,
            )
            return None

        self._mark_playback("local_cache", remote, local=local)
        logging.info(
            "audio_asset_hit bot_code=%s text_hash=%s file_path=%s",
            self._robot_code,
            asset.get("text_hash"),
            asset.get("file_path"),
        )
        return {"asset": asset, "frame": frame}
