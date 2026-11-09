import asyncio
import logging

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    LLMMessagesAppendFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from collection_agents.events import VoicemailDetectedFrame
from collection_agents.services.voicemail_prompts import (
    build_standard_voicemail_prompt,
)
from collection_agents.services.voicemail_player import FixedVoicemailPlayer


class StandardVoicemailHandler(FrameProcessor):
    def __init__(
        self,
        *,
        mode,
        locale="",
        on_detected=None,
        finalize_stt_cb=None,
        instant_close_cb=None,
        fixed_text=None,
        fixed_audio_lookup=None,
        fixed_done_cb=None,
    ):
        super().__init__()
        self._mode = mode
        self._locale = locale
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
                messages=[
                    {
                        "role": "system",
                        "content": build_standard_voicemail_prompt(self._locale),
                    }
                ],
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
                pending_frame = self._pending
                self._pending = False
                await self._dispatch(pending_frame, FrameDirection.DOWNSTREAM)
            elif self._player:
                await self._player.on_bot_stopped(self.push_frame, FrameDirection.DOWNSTREAM)
        elif isinstance(frame, VoicemailDetectedFrame) and not self._detected:
            self._detected = True
            await self._call(self._on_detected)
            if self._finalize_stt_cb:
                try:
                    await self._call(self._finalize_stt_cb)
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

        if isinstance(frame, (EndFrame, CancelFrame)) and self._player:
            await self._player.cleanup()
        await self.push_frame(frame, direction)

    async def cleanup(self):
        await super().cleanup()
        if self._player:
            await self._player.cleanup()
