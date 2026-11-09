import array
import asyncio
import json
import math
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator, Optional

from loguru import logger

from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.frames.frames import CancelFrame, EndFrame, StartFrame
from pipecat.frames.frames import VADUserStoppedSpeakingFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import STTService
from pipecat.utils.time import time_now_iso8601

try:
    from vosk import KaldiRecognizer, Model, SetLogLevel
except ModuleNotFoundError as e:
    logger.error(f"Exception: {e}")
    logger.error("In order to use Vosk, you need to `pip install vosk`.")
    raise

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
        # (bot_beta creates this service in async context), so the recognizer is
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
        ~5s of trailing silence, which makes the final transcript arrive far
        too late for turn management (STTGatedInterruptor gives up after 3s
        and the bot goes silent). Flushing with FinalResult() on VAD stop
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
