"""本地 STT 相关实现合集。

三块内容，原来分散在 vosk_stt.py / shadow_stt.py / asr_compare.py：

1. ``VoskSTTService`` —— 本地 Vosk 实时语音识别服务（pipecat STTService）。
2. ``DeepgramShadowDecoder`` / ``ShadowDecodeTap`` / ``VoskFinalCollector``
   —— Deepgram 影子解码器，用于与线上 Vosk 做 A/B 精度对比；
   完全 best-effort，绝不影响通话主链路。
3. ``save_asr_shadow_compare`` —— 通话结束时把双引擎全文落库
   （asr_shadow_compare 表），供离线对比。
"""

import array
import asyncio
import json
import logging
import math
import os
import time
import time as _time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator, List, Optional

from loguru import logger
from sqlalchemy import text

from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import STTService
from pipecat.utils.time import time_now_iso8601

from collection_agents.storage.db import MysqlClient

try:
    from vosk import KaldiRecognizer, Model, SetLogLevel
except ModuleNotFoundError as e:
    logger.error(f"Exception: {e}")
    logger.error("In order to use Vosk, you need to `pip install vosk`.")
    raise

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


# ---------------------------------------------------------------------------
# 1. VoskSTTService —— 本地 Vosk 实时识别
# ---------------------------------------------------------------------------

_vosk_model_cache: dict[str, "Model"] = {}
_vosk_model_lock = asyncio.Lock()

# Dedicated thread pool for Vosk work: model loading and KaldiRecognizer.AcceptWaveform
# are blocking/CPU-heavy calls. Running them on the default executor risks starving
# other run_in_executor users in the process.
_vosk_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vosk")

# Rough P99 speech-end -> final transcript latency for local Vosk recognition (seconds).
_VOSK_TTFS_P99 = 0.3

# Fallback flush delay after the partial transcript stops changing (seconds).
# Some carrier gateways apply silence suppression (DTX): once the caller goes
# quiet, no more audio frames arrive at all. The pipecat VAD only advances its
# state machine on received buffers, so it never accumulates stop_secs of
# silence and VADUserStoppedSpeakingFrame is never emitted — which is the ONLY
# fast finalize path for Vosk (Kaldi's own endpointer needs ~5s of trailing
# silence). The utterance then stays a partial forever and is lost on hangup.
# This timer forces FinalResult() when the decode has been stable for a while,
# slightly longer than VAD stop_secs so the normal VAD path wins when it works.
_PARTIAL_IDLE_FLUSH_SEC = 0.9

# Empty-decode diagnostic logging (see _maybe_log_empty_partial): only report
# when the chunk actually carries energy (RMS above this, 16-bit scale —
# ~-44 dBFS), and at most once per interval so steady silence stays quiet.
_EMPTY_PARTIAL_RMS_MIN = 200
_EMPTY_PARTIAL_LOG_INTERVAL_SEC = 2.0


def _pcm_rms(audio: bytes) -> float:
    """RMS of a 16-bit PCM chunk (cheap: chunks are ~256 samples)."""
    try:
        samples = array.array("h")
        samples.frombytes(audio[: len(audio) // 2 * 2])
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples))
    except Exception:
        return 0.0


async def _get_or_load_model(model_path: str) -> "Model":
    if model_path in _vosk_model_cache:
        logger.info(f"Vosk model loaded (from cache): {model_path}")
        return _vosk_model_cache[model_path]
    async with _vosk_model_lock:
        if model_path not in _vosk_model_cache:
            loop = asyncio.get_running_loop()
            t0 = asyncio.get_running_loop().time()
            model = await loop.run_in_executor(_vosk_executor, lambda: Model(model_path))
            elapsed = asyncio.get_running_loop().time() - t0
            _vosk_model_cache[model_path] = model
            logger.info(
                f"Vosk model loaded successfully from {model_path} (took {elapsed:.2f}s)"
            )
        else:
            logger.info(f"Vosk model loaded (from cache): {model_path}")
        return _vosk_model_cache[model_path]


class VoskSTTService(STTService):
    """Local Vosk STT service.

    Model loading happens in a background task. While the model is not ready,
    incoming audio is dropped instead of blocking: STTService processes every
    AudioRawFrame inline, so awaiting inside run_stt() would stall the frame
    queue and starve everything downstream (TTS included).
    """

    def __init__(self, *, model_path: str, sample_rate: int = 8000, grammar: Optional[list] = None, **kwargs):
        # Initialize all STTSettings fields explicitly (store mode requires no
        # NOT_GIVEN fields; AIService.start() logs an ERROR otherwise).
        # Vosk derives the language from the model itself, so language=None.
        kwargs.setdefault(
            "settings",
            STTSettings(model=os.path.basename(model_path.rstrip("/")), language=None),
        )
        super().__init__(
            sample_rate=sample_rate,
            ttfs_p99_latency=_VOSK_TTFS_P99,
            **kwargs,
        )
        self._model_path = model_path
        self._init_sample_rate = sample_rate
        self._recognizer: Optional[KaldiRecognizer] = None
        self._ready = asyncio.Event()
        self._load_started = False
        self._not_ready_warned = False
        # Grammar-constrained decoding (confirmation turns). None = free decode.
        # SetGrammar() is unusable: libvosk 0.3.44/0.3.45 do not export
        # vosk_recognizer_set_grm, so switching grammar means rebuilding the
        # recognizer via the constructor (cheap: only compiles the tiny grammar).
        self._grammar = grammar
        # Serializes ALL native calls into this service's recognizer. The
        # shared executor does NOT provide mutual exclusion: with 4 workers,
        # run_stt's AcceptWaveform and the VAD-stop FinalResult/Reset can run
        # concurrently on the same KaldiRecognizer, which Kaldi does not
        # survive (feature-window.cc assertion abort / SIGSEGV in prod).
        self._recognizer_lock = asyncio.Lock()
        # Fallback finalize when the VAD stop never comes (DTX / audio
        # starvation, see _PARTIAL_IDLE_FLUSH_SEC). Armed/reset on every
        # partial change, cancelled on any finalize path.
        self._idle_flush_task: Optional[asyncio.Task] = None
        self._last_partial_text: str = ""
        # Graveyard for recognizers retired by set_grammar. Native calls run
        # in the shared executor and a queued closure may still execute after
        # the swap; if the old Python wrapper were GC'd, its KaldiRecognizer
        # would be freed underneath that call -> SIGSEGV (exit 139). Keep
        # retired instances alive for the lifetime of the call instead.
        self._retired_recognizers: list = []
        # Throttle for empty-decode diagnostics (monotonic seconds of last log).
        self._last_empty_partial_log: float = 0.0
        SetLogLevel(-1)

        # Kick off model loading immediately when constructed inside an event loop
        # (standard creates this service in async context), so the recognizer is
        # ready as early as possible. start() re-triggers idempotently.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            self._ensure_model_loading()

    def _ensure_model_loading(self):
        """Start the background model load exactly once."""
        if self._load_started:
            return
        self._load_started = True
        # self.create_task() is only usable once the processor's TaskManager is
        # wired up (i.e. from start() onwards); fall back to a bare task when
        # called from __init__.
        if getattr(self, "_task_manager", None) is not None:
            self.create_task(self._load_model(), name="vosk_model_load")
        else:
            asyncio.ensure_future(self._load_model())

    async def _load_model(self):
        try:
            if not os.path.isdir(self._model_path):
                logger.error(
                    f"VoskSTTService: model path does not exist: {self._model_path}. "
                    "Audio will be dropped; check asr_config['model_path']."
                )
                return
            logger.info(f"VoskSTTService: loading model from {self._model_path} ...")
            sr = self._init_sample_rate or self._sample_rate
            model = await _get_or_load_model(self._model_path)
            loop = asyncio.get_running_loop()
            grammar_json = self._grammar_json()

            def _build():
                if grammar_json:
                    return KaldiRecognizer(model, sr, grammar_json)
                return KaldiRecognizer(model, sr)

            self._recognizer = await loop.run_in_executor(_vosk_executor, _build)
            logger.info(
                f"VoskSTTService: model load success, recognizer ready at {sr}Hz"
                + (", grammar-constrained" if grammar_json else "")
            )
        except Exception as e:
            logger.error(f"VoskSTTService: model load failed: {e}")
        finally:
            self._ready.set()

    def _grammar_json(self) -> Optional[str]:
        """Grammar as the JSON array string vosk expects, or None for free decode.

        '[unk]' lets out-of-vocabulary words still be decoded instead of
        forcing every utterance onto a listed phrase. The model vocabulary is
        unaccented, so phrases must be too (accented words are silently
        dropped with an 'Ignoring word missing in vocabulary' warning).
        """
        if not self._grammar:
            return None
        phrases = ["[unk]"] + [p for p in self._grammar if p and p != "[unk]"]
        return json.dumps(phrases)

    async def set_grammar(self, phrases: Optional[list]) -> bool:
        """Switch between grammar-constrained and free decoding mid-call.

        Rebuilds the recognizer (SetGrammar() is not exported by libvosk).
        Call only at turn boundaries: the rebuild discards the recognizer's
        buffered silence, which is safe while nobody is speaking. Returns
        False if the recognizer is not ready yet.
        """
        if not self._ready.is_set():
            logger.warning("VoskSTTService: set_grammar before recognizer ready, ignored")
            return False
        self._grammar = phrases
        loop = asyncio.get_running_loop()
        model = await _get_or_load_model(self._model_path)
        sr = self._init_sample_rate or self._sample_rate
        grammar_json = self._grammar_json()

        def _build():
            if grammar_json:
                return KaldiRecognizer(model, sr, grammar_json)
            return KaldiRecognizer(model, sr)

        async with self._recognizer_lock:
            # Retire the old recognizer instead of dropping the reference: a
            # run_stt/_finalize closure already queued in the executor may
            # still be holding it mid-call into Kaldi, and GC of the wrapper
            # frees the native object underneath (see _retired_recognizers).
            if self._recognizer is not None:
                self._retired_recognizers.append(self._recognizer)
            self._recognizer = await loop.run_in_executor(_vosk_executor, _build)
        # The rebuild discarded any buffered audio; drop stale partial state so
        # the idle flush timer doesn't fire against the fresh recognizer.
        self._cancel_idle_flush()
        self._last_partial_text = ""
        logger.info(
            f"VoskSTTService: recognizer rebuilt with "
            + (f"grammar ({len(phrases)} phrases)" if grammar_json else "free decoding")
        )
        return True

    async def start(self, frame: StartFrame):
        await super().start(frame)
        self._ensure_model_loading()

    async def stop(self, frame: EndFrame):
        await self._flush_on_shutdown("stop")
        await super().stop(frame)
        self._recognizer = None
        self._load_started = False

    async def cancel(self, frame: CancelFrame):
        await self._flush_on_shutdown("cancel")
        await super().cancel(frame)
        self._recognizer = None
        self._load_started = False

    async def _flush_on_shutdown(self, reason: str):
        """Best-effort flush of a pending partial so the last user utterance
        still reaches the conversation record / intent classification, even
        when the call ends before the VAD stop or idle timer fires (e.g. the
        customer says "bueno" and hangs up right away). Never raises: the
        pipeline may already be tearing down and refuse frames.
        """
        self._cancel_idle_flush()
        had_pending = bool(self._last_partial_text)
        self._last_partial_text = ""
        if not had_pending or not self._ready.is_set() or not self._recognizer:
            return
        recognizer = self._recognizer
        loop = asyncio.get_running_loop()

        def _finalize():
            return json.loads(recognizer.FinalResult()).get("text", "").strip()

        try:
            async with self._recognizer_lock:
                text = await loop.run_in_executor(_vosk_executor, _finalize)
            if text:
                logger.info(
                    f"VoskSTTService: flushing pending partial on {reason}: {text!r}"
                )
                await self.push_frame(
                    TranscriptionFrame(
                        text=text,
                        user_id=self._user_id,
                        timestamp=time_now_iso8601(),
                        finalized=True,
                    )
                )
        except Exception as e:
            logger.debug(f"VoskSTTService: flush on {reason} failed (ignored): {e}")

    def can_generate_metrics(self) -> bool:
        """Report TTFB/processing metrics like the cloud STT services do.

        Without this, FrameProcessor.can_generate_metrics() stays False and the
        VAD-stop -> final-transcript TTFB is never logged or collected, leaving
        Vosk calls with no ASR latency signal in the logs.
        """
        return True

    async def _handle_vad_user_stopped_speaking(self, frame: VADUserStoppedSpeakingFrame):
        """Flush the recognizer as soon as the VAD says the user stopped.

        Kaldi's internal endpointer only fires AcceptWaveform()==True after
        ~5s of trailing silence, which makes the final transcript arrive too
        late for turn management. Flushing with FinalResult() on VAD stop
        delivers the final within ~0.3s; Reset() starts a clean next utterance.
        """
        await super()._handle_vad_user_stopped_speaking(frame)
        await self._flush_finalizer("VAD stop")

    def _cancel_idle_flush(self):
        # Never cancel the currently running task: _idle_flush_handler calls
        # _flush_finalizer -> _cancel_idle_flush on itself, and self-cancellation
        # would abort the flush with CancelledError at its next await.
        current = asyncio.current_task()
        if (
            self._idle_flush_task
            and self._idle_flush_task is not current
            and not self._idle_flush_task.done()
        ):
            self._idle_flush_task.cancel()
        self._idle_flush_task = None

    def _arm_idle_flush(self):
        """(Re)start the fallback finalize timer after the partial changed.

        Only fires when no VAD stop arrives within _PARTIAL_IDLE_FLUSH_SEC of
        the last partial change — i.e. the upstream stopped sending audio
        (silence suppression) and the VAD state machine can never advance.
        """
        self._cancel_idle_flush()
        if getattr(self, "_task_manager", None) is None:
            return  # not started yet; nothing to guard
        self._idle_flush_task = self.create_task(
            self._idle_flush_handler(), name="vosk_idle_flush"
        )

    async def _idle_flush_handler(self):
        try:
            await asyncio.sleep(_PARTIAL_IDLE_FLUSH_SEC)
            if self._last_partial_text:
                logger.debug(
                    f"VoskSTTService: no VAD stop {_PARTIAL_IDLE_FLUSH_SEC}s after last partial "
                    f"{self._last_partial_text!r}, flushing via fallback timer "
                    "(possible upstream DTX/audio gap)"
                )
                await self._flush_finalizer("idle fallback")
        except asyncio.CancelledError:
            pass

    async def _flush_finalizer(self, reason: str):
        """Force FinalResult() + Reset() and push the finalized transcript.

        Shared by the VAD-stop path and the idle fallback timer. Any pending
        fallback timer is cancelled first so only one path flushes per utterance.
        """
        self._cancel_idle_flush()
        self._last_partial_text = ""

        if not self._ready.is_set() or not self._recognizer:
            return

        loop = asyncio.get_running_loop()
        recognizer = self._recognizer
        logger.debug(f"VoskSTTService: {reason}, finalizing recognizer")

        def _finalize():
            text = json.loads(recognizer.FinalResult()).get("text", "").strip()
            recognizer.Reset()
            return text

        try:
            async with self._recognizer_lock:
                text = await loop.run_in_executor(_vosk_executor, _finalize)
        except Exception as e:
            logger.error(f"VoskSTTService: finalize on {reason} failed: {e}")
            return
        logger.debug(f"VoskSTTService: finalize returned {text!r}")
        if not text:
            return
        logger.debug(f"VoskSTTService: finalized on {reason}: {text!r}")
        # finalized=True: this flush is the utterance's final transcript. It lets
        # STTService report TTFB immediately (instead of after its 2s timeout) and
        # lets turn-stop strategies end the user turn without waiting.
        await self.push_frame(
            TranscriptionFrame(
                text=text,
                user_id=self._user_id,
                timestamp=time_now_iso8601(),
                finalized=True,
            )
        )

    def _maybe_log_empty_partial(self, audio: bytes):
        """Diagnose empty decodes: distinguish 'no audio' from 'model decoded
        nothing'. Only logs when the chunk carries real energy (a word too
        short/quiet for free decoding still has RMS above silence), throttled
        to at most one line per _EMPTY_PARTIAL_LOG_INTERVAL_SEC."""
        rms = _pcm_rms(audio)
        if rms < _EMPTY_PARTIAL_RMS_MIN:
            return
        now = _time.monotonic()
        if now - self._last_empty_partial_log < _EMPTY_PARTIAL_LOG_INTERVAL_SEC:
            return
        self._last_empty_partial_log = now
        logger.debug(
            f"VoskSTTService: empty partial despite audio energy "
            f"(rms={rms:.0f}, grammar={'on' if self._grammar else 'off'}) — "
            "word likely too short/quiet for decoding"
        )

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        # Never block in run_stt: STTService awaits it for every AudioRawFrame,
        # so waiting for the model here would stall the frame queue and prevent
        # downstream frames (e.g. the queued welcome LLMTextFrame -> TTS) from
        # ever reaching the TTS service. Drop audio until the recognizer is ready.
        if not self._ready.is_set() or not self._recognizer:
            if not self._not_ready_warned:
                self._not_ready_warned = True
                logger.warning(
                    "VoskSTTService: recognizer not ready, dropping incoming audio"
                )
            return

        loop = asyncio.get_running_loop()
        recognizer = self._recognizer

        def _process():
            if recognizer.AcceptWaveform(audio):
                result = json.loads(recognizer.Result())
                return "final", result.get("text", "").strip()
            else:
                partial = json.loads(recognizer.PartialResult())
                return "partial", partial.get("partial", "").strip()

        async with self._recognizer_lock:
            kind, text = await loop.run_in_executor(_vosk_executor, _process)

        if not text:
            if kind == "partial":
                # Empty decode is invisible without this: it is the difference
                # between "no audio reaching Vosk" and "audio reached Vosk but
                # the model decoded nothing" (short/quiet word under free
                # decoding). Log with the chunk's RMS, throttled so silence
                # doesn't spam.
                self._maybe_log_empty_partial(audio)
            return

        ts = time_now_iso8601()
        if kind == "final":
            # Kaldi's own endpointer fired: clear partial-tracking state.
            self._cancel_idle_flush()
            self._last_partial_text = ""
            yield TranscriptionFrame(text=text, user_id=self._user_id, timestamp=ts)
        else:
            # (Re)arm the fallback flush whenever the partial advances, so the
            # utterance is finalized even if the VAD stop never arrives.
            if text != self._last_partial_text:
                self._last_partial_text = text
                self._arm_idle_flush()
            yield InterimTranscriptionFrame(text=text, user_id=self._user_id, timestamp=ts)


# ---------------------------------------------------------------------------
# 2. Deepgram 影子解码（与 Vosk 做 A/B 对比，绝不影响主链路）
#
# Design constraints (must hold, or the shadow can hurt the live call):
#   - Fully best-effort: any failure (no API key, connect error, send error) is
#     swallowed and logged at DEBUG/WARNING. The shadow is never allowed to raise
#     into the pipeline.
#   - Never blocks the audio path: frames are handed to a background consumer via
#     a bounded queue; if Deepgram is slow the queue drops (shadow data is
#     disposable, live audio is not).
#   - Isolated from the pipeline: it is NOT an STTService and pushes no frames.
#     It only reads audio and collects text.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 3. 影子对比结果落库（asr_shadow_compare 表）
#
# Written once per call, at end of call, for robots running local Vosk with the
# Deepgram shadow enabled. Fully best-effort: any DB error is logged and
# swallowed so it can never affect the call.
# ---------------------------------------------------------------------------

_INSERT_SQL = text(
    """
    INSERT INTO asr_shadow_compare
        (call_uuid, robot_code, language, vosk_model, deepgram_model,
         vosk_text, deepgram_text, vosk_segments, deepgram_segments, ctime1)
    VALUES
        (:call_uuid, :robot_code, :language, :vosk_model, :deepgram_model,
         :vosk_text, :deepgram_text, :vosk_segments, :deepgram_segments, :ctime1)
    """
)


def save_asr_shadow_compare(
    *,
    call_uuid,
    robot_code,
    language,
    vosk_model,
    deepgram_model,
    vosk_text,
    deepgram_text,
    vosk_segments=None,
    deepgram_segments=None,
):
    """Insert one comparison row for the call. Never raises."""
    try:
        params = {
            "call_uuid": call_uuid or "",
            "robot_code": robot_code or "",
            "language": language or "",
            "vosk_model": vosk_model or "",
            "deepgram_model": deepgram_model or "",
            "vosk_text": (vosk_text or "")[:8000],
            "deepgram_text": (deepgram_text or "")[:8000],
            "vosk_segments": json.dumps(vosk_segments or [], ensure_ascii=False),
            "deepgram_segments": json.dumps(deepgram_segments or [], ensure_ascii=False),
            "ctime1": int(time.time() * 1000),
        }
        MysqlClient.execute(_INSERT_SQL, params)
        logging.info(
            "asr_shadow_compare inserted for call_uuid=%s (vosk=%d chars, deepgram=%d chars)",
            params["call_uuid"],
            len(params["vosk_text"]),
            len(params["deepgram_text"]),
        )
    except Exception as exc:
        logging.warning(f"save_asr_shadow_compare failed (ignored): {exc}")
