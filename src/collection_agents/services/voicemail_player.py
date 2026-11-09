import asyncio
import logging

from pipecat.frames.frames import LLMMessagesAppendFrame, OutputAudioRawFrame, TTSSpeakFrame

from collection_agents.services.audio_assets import split_voicemail_segments


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
