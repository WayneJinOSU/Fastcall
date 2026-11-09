"""Benchmark Vosk model load + recognition timing.

Run:
    python test_vosk_load.py [model_path]

Measures:
  1. Cold model load time (Model(path)) — the number that matters for the
     "audio dropped while loading" window on the first call.
  2. KaldiRecognizer construction time.
  3. A real recognition pass over generated audio (sanity check).
"""
import sys
import time
import json
import math
import struct

MODEL_PATH = sys.argv[1] if len(sys.argv) > 1 else "/Users/wjq/Downloads/vosk-model-es-0.42"
SAMPLE_RATE = 8000


def make_sine_pcm(seconds: float, freq: float = 220.0, sr: int = SAMPLE_RATE) -> bytes:
    """Generate 16-bit mono PCM sine wave (a stand-in for speech audio)."""
    n = int(seconds * sr)
    frames = bytearray()
    for i in range(n):
        v = int(12000 * math.sin(2 * math.pi * freq * i / sr))
        frames += struct.pack("<h", v)
    return bytes(frames)


def main():
    from vosk import Model, KaldiRecognizer, SetLogLevel

    SetLogLevel(0)  # keep Kaldi's own logs visible so we can see what's slow

    print(f"\n=== Vosk model load benchmark ===")
    print(f"model path : {MODEL_PATH}")
    print(f"sample rate: {SAMPLE_RATE}Hz\n")

    # --- 1. Cold model load ---
    t0 = time.perf_counter()
    model = Model(MODEL_PATH)
    load_secs = time.perf_counter() - t0
    print(f"\n[1] Model() cold load      : {load_secs:.2f}s")

    # --- 2. Recognizer construction ---
    t0 = time.perf_counter()
    rec = KaldiRecognizer(model, SAMPLE_RATE)
    rec_secs = time.perf_counter() - t0
    print(f"[2] KaldiRecognizer() build: {rec_secs:.3f}s")

    # --- 3. Recognition over generated audio ---
    audio = make_sine_pcm(2.0)
    chunk = SAMPLE_RATE * 2 // 25  # ~40ms chunks, like the real pipeline
    t0 = time.perf_counter()
    text = ""
    for off in range(0, len(audio), chunk):
        if rec.AcceptWaveform(audio[off:off + chunk]):
            text = json.loads(rec.Result()).get("text", "")
    final = json.loads(rec.FinalResult()).get("text", "")
    recog_secs = time.perf_counter() - t0
    print(f"[3] Recognize 2.0s audio   : {recog_secs:.3f}s "
          f"(text={text!r} final={final!r})")

    print(f"\n=== Summary ===")
    print(f"Cold load (first call drops audio for this long): {load_secs:.2f}s")
    print(f"NOTE: sine wave is not real speech, so transcript is expected to be empty.")
    print(f"      The load time is the number to watch — if it's large, preheat the")
    print(f"      model at process startup so the first call doesn't drop user audio.")


if __name__ == "__main__":
    main()
