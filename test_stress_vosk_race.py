"""Stress test: concurrent native calls into one KaldiRecognizer.

VoskSTTService submits AcceptWaveform (run_stt) and FinalResult/Reset
(VAD-stop finalize) to a shared ThreadPoolExecutor(max_workers=4). Same
executor != mutual exclusion: with 4 workers, two tasks touching the SAME
recognizer can run in parallel, which Kaldi does not survive (SIGSEGV).

This script drives one service from many concurrent tasks (audio frames +
VAD stops interleaved) to trigger the race. Expected: process dies with
signal 11 before the fix; survives after per-recognizer serialization.

Run: python test_stress_vosk_race.py [model_path] [wav_path]
"""
import asyncio
import audioop
import sys
import time
import wave

sys.path.insert(0, ".")

from pipecat.frames.frames import StartFrame, VADUserStoppedSpeakingFrame
from bot_beta_modules.vosk_stt import VoskSTTService

MODEL = sys.argv[1] if len(sys.argv) > 1 else "/Users/wjq/Downloads/vosk-model-es-0.42"
WAV = sys.argv[2] if len(sys.argv) > 2 else "assets/14287.wav"
WORKERS = 8          # concurrent tasks hammering the same recognizer
DURATION = 45.0      # seconds of hammering


def load_chunks(path, chunk_ms=32):
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    if channels == 2:
        raw = audioop.tomono(raw, 2, 0.5, 0.5)
    if sr != 8000:
        raw, _ = audioop.ratecv(raw, 2, 1, sr, 8000, None)
    n = int(8000 * chunk_ms / 1000) * 2
    return [raw[i:i + n] for i in range(0, len(raw), n)]


async def hammer(service, chunks, idx, deadline):
    i = idx
    while time.monotonic() < deadline:
        chunk = chunks[i % len(chunks)]
        i += 1
        async for _ in service.run_stt(chunk):
            pass
        if i % 25 == 0:
            # VAD stop interleaved with in-flight audio processing: the
            # finalize path racing AcceptWaveform.
            await service._handle_vad_user_stopped_speaking(VADUserStoppedSpeakingFrame())
        await asyncio.sleep(0)  # let other workers interleave


async def main():
    chunks = load_chunks(WAV)
    service = VoskSTTService(model_path=MODEL, sample_rate=8000)
    await service.start(StartFrame())
    await service._ready.wait()
    print("model ready; hammering with", WORKERS, "concurrent tasks for", DURATION, "s", flush=True)
    deadline = time.monotonic() + DURATION
    await asyncio.gather(*(hammer(service, chunks, i, deadline) for i in range(WORKERS)))
    print("SURVIVED: no crash under concurrent native access", flush=True)


asyncio.run(main())
