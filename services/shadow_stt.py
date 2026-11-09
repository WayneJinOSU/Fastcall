"""Shadow Deepgram decoder for A/B comparing against the live Vosk ASR.

Some robots run local Vosk (small model) as the real-time ASR. To measure how
much accuracy we lose versus a cloud engine, this module runs Deepgram as a
*shadow* on the exact same audio: it never feeds the LLM, never interrupts the
bot, never affects turn-taking. It only accumulates Deepgram's transcripts so
they can be persisted next to the Vosk transcripts at end of call for offline
comparison.

Design constraints (must hold, or the shadow can hurt the live call):
  - Fully best-effort: any failure (no API key, connect error, send error) is
    swallowed and logged at DEBUG/WARNING. The shadow is never allowed to raise
    into the pipeline.
  - Never blocks the audio path: frames are handed to a background consumer via
    a bounded queue; if Deepgram is slow the queue drops (shadow data is
    disposable, live audio is not).
  - Isolated from the pipeline: it is NOT an STTService and pushes no frames.
    It only reads audio and collects text.
"""
import asyncio
import logging
from typing import List, Optional

from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

try:
    from deepgram import (
        DeepgramClient,
        DeepgramClientOptions,
        LiveOptions,
        LiveTranscriptionEvents,
    )

    _DEEPGRAM_AVAILABLE = True
except Exception as _e:  # pragma: no cover - deepgram missing should not be fatal
    _DEEPGRAM_AVAILABLE = False
    logging.warning(f"[ShadowSTT] deepgram SDK unavailable, shadow disabled: {_e}")


# Bound the send queue so a stalled Deepgram connection can never grow memory
# without limit. ~300 chunks * 32ms ≈ 10s of audio buffered; beyond that we drop
# (shadow gaps are acceptable, live-call impact is not).
_QUEUE_MAXSIZE = 300


class DeepgramShadowDecoder:
    """Owns a Deepgram websocket and collects its transcripts.

    Lifecycle: start() -> feed(audio) many times -> stop() -> .text
    All methods are safe to call even if start() failed; they no-op.
    """

    def __init__(self, *, api_key: str, language: str, model: str, sample_rate: int = 8000):
        self._api_key = api_key
        self._language = language
        self._model = model
        self._sample_rate = sample_rate
        self._client = None
        self._connection = None
        self._queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._consumer: Optional[asyncio.Task] = None
        self._finals: List[str] = []
        self._connected = False
        self._dropped_chunks = 0
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def text(self) -> str:
        """Deepgram's full-call transcript (final segments joined, in order)."""
        return " ".join(t for t in self._finals if t).strip()

    @property
    def final_segments(self) -> List[str]:
        return list(self._finals)

    async def start(self):
        if not _DEEPGRAM_AVAILABLE:
            return
        if not self._api_key:
            logging.info("[ShadowSTT] DEEPGRAM_API_KEY not set, shadow disabled")
            return
        try:
            self._client = DeepgramClient(
                self._api_key,
                config=DeepgramClientOptions(options={"keepalive": "true"}),
            )
            self._connection = self._client.listen.asyncwebsocket.v("1")
            self._connection.on(LiveTranscriptionEvents.Transcript, self._on_message)
            self._connection.on(LiveTranscriptionEvents.Error, self._on_error)
            options = LiveOptions(
                encoding="linear16",
                language=self._language,
                model=self._model,
                sample_rate=self._sample_rate,
                channels=1,
                interim_results=False,  # shadow only needs finals
                smart_format=False,
                punctuate=False,
            )
            ok = await self._connection.start(options=options)
            if not ok:
                logging.warning("[ShadowSTT] Deepgram shadow connect returned False, disabled")
                self._connection = None
                return
            self._connected = True
            self._enabled = True
            self._consumer = asyncio.create_task(self._consume(), name="deepgram_shadow_consumer")
            logging.info(
                f"[ShadowSTT] Deepgram shadow connected (lang={self._language} model={self._model})"
            )
        except Exception as exc:
            logging.warning(f"[ShadowSTT] start failed, shadow disabled: {exc}")
            self._connected = False
            self._connection = None

    def feed(self, audio: bytes):
        """Non-blocking hand-off of one audio chunk. Drops on overflow."""
        if not self._enabled or not audio:
            return
        try:
            self._queue.put_nowait(audio)
        except asyncio.QueueFull:
            self._dropped_chunks += 1

    async def _consume(self):
        while True:
            chunk = await self._queue.get()
            if chunk is None:  # sentinel: stop requested
                self._queue.task_done()
                break
            try:
                if self._connection is not None:
                    await self._connection.send(chunk)
            except Exception as exc:
                logging.debug(f"[ShadowSTT] send failed (ignored): {exc}")
            finally:
                self._queue.task_done()

    async def _on_message(self, *args, **kwargs):
        try:
            result = kwargs["result"]
            if not result.channel.alternatives:
                return
            if result.is_final:
                transcript = result.channel.alternatives[0].transcript
                if transcript:
                    self._finals.append(transcript)
        except Exception as exc:
            logging.debug(f"[ShadowSTT] on_message failed (ignored): {exc}")

    async def _on_error(self, *args, **kwargs):
        logging.warning(f"[ShadowSTT] Deepgram error (shadow continues): {kwargs.get('error')}")

    async def stop(self):
        """Flush remaining audio, close the connection. Best-effort."""
        if not self._enabled:
            return
        self._enabled = False
        try:
            # Let queued audio drain before finishing so late finals arrive.
            await asyncio.wait_for(self._queue.join(), timeout=2.0)
        except Exception:
            pass
        try:
            await self._queue.put(None)
            if self._consumer and not self._consumer.done():
                await asyncio.wait_for(self._consumer, timeout=2.0)
        except Exception:
            if self._consumer and not self._consumer.done():
                self._consumer.cancel()
        try:
            if self._connection is not None and await self._connection.is_connected():
                # finalize() flushes any in-progress segment as a final.
                await self._connection.finalize()
                await self._connection.finish()
        except Exception as exc:
            logging.debug(f"[ShadowSTT] stop failed (ignored): {exc}")
        finally:
            self._connection = None
            if self._dropped_chunks:
                logging.info(f"[ShadowSTT] dropped {self._dropped_chunks} chunks (queue overflow)")


class ShadowDecodeTap(FrameProcessor):
    """Pipeline tap that mirrors input audio into a DeepgramShadowDecoder.

    Placed after the transport input so it sees InputAudioRawFrame. It forwards
    every frame downstream unchanged — it is purely observational and never
    consumes, mutates, or emits frames of its own.
    """

    def __init__(self, decoder: DeepgramShadowDecoder, **kwargs):
        super().__init__(**kwargs)
        self._decoder = decoder

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, AudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            # Tap only: hand audio to the shadow decoder, never block on it.
            self._decoder.feed(frame.audio)
        await self.push_frame(frame, direction)


class VoskFinalCollector(FrameProcessor):
    """Collects the FINAL transcripts the live Vosk STT emits during the call.

    Placed right after the STT service. Observational only: forwards every
    frame unchanged. Interim results are ignored so the collected text matches
    what actually reached the LLM / conversation record.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._finals: List[str] = []

    @property
    def text(self) -> str:
        return " ".join(t for t in self._finals if t).strip()

    @property
    def final_segments(self) -> List[str]:
        return list(self._finals)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, TranscriptionFrame)
            and not isinstance(frame, InterimTranscriptionFrame)
            and direction == FrameDirection.DOWNSTREAM
        ):
            text = (frame.text or "").strip()
            if text:
                self._finals.append(text)
        await self.push_frame(frame, direction)
