"""Latency test for VoskSTTService VAD-stop finalization.

Run:
    python test/test_vosk_finalize.py [model_path] [wav_path]

Feeds resampled telephony-rate audio straight into VoskSTTService, then a
VADUserStoppedSpeakingFrame, and asserts the final TranscriptionFrame arrives
promptly (<1.5s) instead of waiting for Kaldi's ~5s internal endpointing.
Drives the service directly (no PipelineTask) so teardown quirks of a
transport-less pipeline can't mask the measurement.
"""
import asyncio
import audioop
import sys
import time
import wave

sys.path.insert(0, ".")

from pipecat.frames.frames import (
    InputAudioRawFrame,
    StartFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.frames.frames import Frame
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.clocks.system_clock import SystemClock
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams

from collection_agents.services.stt_local import VoskSTTService

MODEL_PATH = sys.argv[1] if len(sys.argv) > 1 else "/Users/wjq/Downloads/vosk-model-es-0.42"
WAV_PATH = sys.argv[2] if len(sys.argv) > 2 else "assets/14287.wav"
CHUNK_MS = 20


def load_pcm_8k(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
        if w.getnchannels() == 2:
            raw = audioop.tomono(raw, 2, 0.5, 0.5)
        if w.getframerate() != 8000:
            raw, _ = audioop.ratecv(raw, 2, 1, w.getframerate(), 8000, None)
    return raw


class SpyVoskSTTService(VoskSTTService):
    """Records transcription frames instead of pushing them into a pipeline."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.final_text = None
        self.final_at = None
        self.finalized = None
        self.ttfb_value = None
        self.ttfb_at = None
        self.interims = []

    async def push_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        if isinstance(frame, TranscriptionFrame):
            if self.final_text is None:
                self.final_text = frame.text
                self.final_at = time.monotonic()
                self.finalized = frame.finalized
        elif isinstance(frame, MetricsFrame):
            for data in frame.data:
                if isinstance(data, TTFBMetricsData) and self.ttfb_value is None:
                    self.ttfb_value = data.value
                    self.ttfb_at = time.monotonic()
        elif type(frame).__name__ == "InterimTranscriptionFrame":
            self.interims.append(frame.text)
        await super().push_frame(frame, direction)


async def main():
    pcm = load_pcm_8k(WAV_PATH)
    print(f"audio: {WAV_PATH} -> {len(pcm) / 16000:.2f}s @8kHz")

    stt = SpyVoskSTTService(model_path=MODEL_PATH, sample_rate=8000)
    task_manager = TaskManager()
    task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
    clock = SystemClock()
    clock.start()
    await stt.setup(FrameProcessorSetup(clock=clock, task_manager=task_manager))
    # enable_metrics mirrors production PipelineParams(enable_metrics=True) so
    # the TTFB MetricsFrame path is exercised here too.
    await stt.process_frame(StartFrame(enable_metrics=True), FrameDirection.DOWNSTREAM)
    await asyncio.wait_for(stt._ready.wait(), timeout=60)
    print("model ready", flush=True)

    await stt.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    step = 8000 * 2 * CHUNK_MS // 1000
    for i in range(0, len(pcm), step):
        await stt.process_frame(
            InputAudioRawFrame(audio=pcm[i : i + step], sample_rate=8000, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
    stop_at = time.monotonic()
    # stop_secs must be >0: STTService skips TTFB measurement when it is 0 (the
    # dataclass default), which is never the case in production where the VAD
    # analyzer fills it with the configured stop threshold.
    await stt.process_frame(
        VADUserStoppedSpeakingFrame(stop_secs=0.5), FrameDirection.DOWNSTREAM
    )
    print(f"VAD stop pushed; interims so far: {stt.interims[-3:]}", flush=True)

    try:
        await asyncio.wait_for(_wait_final(stt), timeout=5)
    except asyncio.TimeoutError:
        pass

    if stt.final_text is None:
        print("FAIL: no final transcription within 5s of VAD stop")
        sys.exit(1)
    delay = stt.final_at - stop_at
    print(f"final text: {stt.final_text!r} (finalized={stt.finalized})")
    print(f"delay after VAD stop: {delay:.3f}s")
    assert delay < 1.5, f"final arrived {delay:.2f}s after VAD stop; expected <1.5s"
    assert stt.finalized is True, "final TranscriptionFrame must be marked finalized"
    print("PASS: final transcript delivered promptly on VAD stop")
    if stt.ttfb_value is not None:
        ttfb_delay = stt.ttfb_at - stop_at
        print(f"PASS: TTFB metrics reported immediately: {stt.ttfb_value:.3f}s "
              f"({ttfb_delay:.3f}s after VAD stop)")
        assert ttfb_delay < 1.5, "TTFB should be reported immediately, not after the 2s timeout"
    else:
        print("WARN: no TTFB MetricsFrame observed (metrics disabled?)")
    await stt.cleanup()


async def _wait_final(stt: SpyVoskSTTService):
    while stt.final_text is None:
        await asyncio.sleep(0.05)


if __name__ == "__main__":
    asyncio.run(main())
