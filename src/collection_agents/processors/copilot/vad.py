
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import (
    InputAudioRawFrame,
    StartFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor


class AICopilotVADProcessor(FrameProcessor):
    """Runs VAD after the copilot protocol router has materialized audio frames."""

    def __init__(self, vad_analyzer, **kwargs):
        super().__init__(**kwargs)
        self._vad_analyzer = vad_analyzer
        self._vad_state = VADState.QUIET

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            sample_rate = getattr(frame, "audio_in_sample_rate", None) or 8000
            self._vad_analyzer.set_sample_rate(sample_rate)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            if not self._vad_analyzer.sample_rate:
                self._vad_analyzer.set_sample_rate(frame.sample_rate or 8000)

            new_vad_state = await self._vad_analyzer.analyze_audio(frame.audio)
            if (
                new_vad_state != self._vad_state
                and new_vad_state not in (VADState.STARTING, VADState.STOPPING)
            ):
                if new_vad_state == VADState.SPEAKING:
                    await self.push_frame(
                        VADUserStartedSpeakingFrame(
                            start_secs=self._vad_analyzer.params.start_secs,
                        ),
                        direction,
                    )
                elif new_vad_state == VADState.QUIET:
                    await self.push_frame(
                        VADUserStoppedSpeakingFrame(
                            stop_secs=self._vad_analyzer.params.stop_secs,
                        ),
                        direction,
                    )
                self._vad_state = new_vad_state

            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
