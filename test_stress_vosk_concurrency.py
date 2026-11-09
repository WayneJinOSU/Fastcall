"""Multi-instance concurrency stress test for VoskSTTService (segfault guard).

Production background: concurrent native calls into ONE KaldiRecognizer used to
SIGSEGV (exit code 139, pod restart loop). Fixed by per-instance
self._recognizer_lock (commit b1e396a). Since then new concurrent paths were
added that must not regress:

  1. idle-flush fallback timer (_arm_idle_flush / _idle_flush_handler) — a
     time-based task racing run_stt's AcceptWaveform with FinalResult+Reset.
  2. shutdown flush (_flush_on_shutdown) — stop()/cancel() racing in-flight
     audio processing.
  3. set_grammar recognizer rebuild — KaldiRecognizer construction racing
     AcceptWaveform on the old recognizer object.

This script simulates a prod pod: N concurrent calls (N service instances)
sharing ONE cached Model and the ONE module-level 4-worker _vosk_executor.
Per instance it runs:
  - WORKERS_PER_INSTANCE audio hammer tasks (run_stt as fast as possible),
  - periodic VAD-stop flushes interleaved with in-flight audio,
  - real idle-flush timer arming (partials change -> timer -> fires if no VAD
    stop within _PARTIAL_IDLE_FLUSH_SEC),
  - a grammar toggle task (set_grammar(list) <-> set_grammar(None)) to force
    recognizer rebuilds mid-hammer,
  - a final stop()/cancel() while audio tasks are still running.

Expected: process survives with exit code 0. Before the locking fix (or if any
native path escapes the lock), it dies with signal 11 (exit code 139).

Run: python test_stress_vosk_concurrency.py [model_path] [wav_path] [duration_sec]
"""
import asyncio
import audioop
import os
import random
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    StartFrame,
    VADUserStoppedSpeakingFrame,
)
from bot_beta_modules.vosk_stt import VoskSTTService

MODEL = sys.argv[1] if len(sys.argv) > 1 else "/Users/wjq/Downloads/vosk-model-small-es-0.42"
WAV = sys.argv[2] if len(sys.argv) > 2 else "assets/14287.wav"
DURATION = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0

INSTANCES = 4            # concurrent "calls" in this process, like prod
WORKERS_PER_INSTANCE = 6 # audio hammer tasks per call
GRAMMAR = ["si", "no", "bueno", "hola", "si soy yo", "ya pague", "[unk]"]

_pushed_counts = {}


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


def wire_stubs(service, idx):
    """Minimal pipeline stand-ins so the service runs outside a real pipeline."""
    pushed = []
    _pushed_counts[idx] = pushed

    async def fake_push(frame, direction=None):
        pushed.append(type(frame).__name__)

    service.push_frame = fake_push
    # _arm_idle_flush only requires _task_manager to be non-None; use real
    # asyncio.create_task so timers genuinely race the audio tasks.
    service._task_manager = object()
    service.create_task = lambda coro, name=None: asyncio.create_task(coro, name=name)
    service._user_id = f"user-{idx}"


async def hammer(service, chunks, seed, deadline, stats):
    rng = random.Random(seed)
    i = seed
    while time.monotonic() < deadline:
        chunk = chunks[i % len(chunks)]
        i += 1
        async for _ in service.run_stt(chunk):
            stats["audio_frames"] += 1
        if i % 25 == 0:
            # VAD stop interleaved with in-flight AcceptWaveform from siblings.
            await service._handle_vad_user_stopped_speaking(VADUserStoppedSpeakingFrame())
            stats["vad_stops"] += 1
        if rng.random() < 0.002:
            # Occasionally simulate the caller going quiet (DTX): stop feeding
            # and let the idle-flush timer fire against concurrent audio from
            # the other hammer tasks of the same instance.
            await asyncio.sleep(1.2)
        await asyncio.sleep(0)


async def grammar_toggler(service, deadline, stats):
    """Force recognizer rebuilds (KaldiRecognizer ctor on the shared model)
    while audio hammering is in flight."""
    on = False
    while time.monotonic() < deadline:
        await asyncio.sleep(0.7)
        on = not on
        await service.set_grammar(GRAMMAR if on else None)
        stats["grammar_switches"] += 1


async def run_instance(idx, chunks, deadline, stats):
    service = VoskSTTService(
        model_path=MODEL, sample_rate=8000, grammar=GRAMMAR if idx % 2 == 0 else None
    )
    wire_stubs(service, idx)
    await service.start(StartFrame())
    await service._ready.wait()

    tasks = [
        asyncio.create_task(hammer(service, chunks, idx * 100 + w, deadline, stats),
                            name=f"hammer-{idx}-{w}")
        for w in range(WORKERS_PER_INSTANCE)
    ]
    tasks.append(asyncio.create_task(grammar_toggler(service, deadline, stats),
                                     name=f"grammar-{idx}"))
    await asyncio.gather(*tasks)

    # Shutdown while stragglers (idle timers, executor jobs) may still be live:
    # half the instances stop(), half cancel() — exercises _flush_on_shutdown
    # racing in-flight native calls.
    frame = EndFrame() if idx % 2 == 0 else CancelFrame()
    if idx % 2 == 0:
        await service.stop(frame)
    else:
        await service.cancel(frame)
    stats["shutdowns"] += 1


async def main():
    chunks = load_chunks(WAV)
    stats = {"audio_frames": 0, "vad_stops": 0, "grammar_switches": 0, "shutdowns": 0}
    print(
        f"model={MODEL}\nwav={WAV} chunks={len(chunks)}\n"
        f"instances={INSTANCES} workers/instance={WORKERS_PER_INSTANCE} "
        f"duration={DURATION}s\n"
        f"shared executor workers=4, shared model cache=1 — hammering...",
        flush=True,
    )
    t0 = time.monotonic()
    deadline = t0 + DURATION
    await asyncio.gather(*(run_instance(i, chunks, deadline, stats) for i in range(INSTANCES)))
    elapsed = time.monotonic() - t0
    finals = sum(1 for v in _pushed_counts.values() for f in v if f == "TranscriptionFrame")
    print(
        f"\nSURVIVED {elapsed:.1f}s — no segfault.\n"
        f"  audio frames fed : {stats['audio_frames']}\n"
        f"  VAD-stop flushes : {stats['vad_stops']}\n"
        f"  grammar rebuilds : {stats['grammar_switches']}\n"
        f"  clean shutdowns  : {stats['shutdowns']}\n"
        f"  final transcripts pushed: {finals}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
    print("EXIT OK", flush=True)
