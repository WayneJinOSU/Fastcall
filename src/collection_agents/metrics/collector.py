"""
Call-level metrics collector and pipecat observer.

Usage:
    collector = CallMetricsCollector(
        call_uuid=call_uuid,
        robot_code=robot_code,
        bot_mode="standard",
        task_start_time=datetime.datetime.now(),
        asr_info={"provider": "deepgram", "model": "nova-2"},
        llm_info={"provider": "deepseek", "model": "deepseek-flash"},
        tts_info={"provider": "cartesia", "model": "sonic-2"},
    )
    task_observers = [CallMetricsObserver(collector)]
    # In finally block:
    collector.finalize(datetime.datetime.now())
    data = collector.to_dict()
"""

import datetime
import json
import re
from typing import Optional

from sqlalchemy import text

from collection_agents.storage.db import MysqlClient
import time

from pipecat.frames.frames import AudioRawFrame, ErrorFrame, InputAudioRawFrame, InterimTranscriptionFrame, MetricsFrame, TextFrame, TranscriptionFrame, VADUserStartedSpeakingFrame
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
import logging


_CARTESIA_CREDIT_USD = 0.000020


_DEFAULT_PRICING = {
    # ASR: Deepgram per-hour rates (keyed as "asr_<model-name-lowercase>")
    "asr_nova-2":              0.23,
    "asr_nova-3":              0.31,   # monolingual
    "asr_nova-3-multilingual": 0.37,
    "asr_flux":                0.31,   # monolingual
    # Cartesia Ink Whisper STT is 1 credit/second; TTS uses the same credit price.
    "cartesia_credit_usd":              _CARTESIA_CREDIT_USD,
    "cartesia_stt_credits_per_second":  1.0,
    "tts_per_char":                     _CARTESIA_CREDIT_USD,
    # LLM: GPT-5.4-mini
    "llm_prompt_per_1k":       0.00075*0.77,    # $0.75 / 1M input tokens
    "llm_completion_per_1k":   0.0045*0.77,      # $4.5   / 1M output tokens
    "llm_cache_read_per_1k":   0.00008*0.77,    # $0.08 / 1M cache-read tokens
    # LLM: DeepSeek V4.1 Flash (API alias: deepseek-flash) — 请根据实际账单确认单价
    "llm_deepseek-flash_prompt_per_1k":       0.00014,   # $0.14 / 1M input tokens
    "llm_deepseek-flash_completion_per_1k":   0.00029,   # $0.294   / 1M output tokens
    "llm_deepseek-flash_cache_read_per_1k":   0.0000029,   # $0.0029 / 1M cache-read tokens
}


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def _count_meaningful_units(text: str) -> int:
    """Count meaningful units in text:
    - CJK characters (Chinese etc.): each character = 1 unit
    - Other languages (English, Spanish, etc.): each word = 1 unit

    Example: "你好 hello world!" → 你(1) 好(1) hello(1) world(1) = 4
    """
    cjk_count = len(_CJK_RE.findall(text))
    non_cjk = _CJK_RE.sub(" ", text)
    word_count = sum(1 for w in non_cjk.split() if any(c.isalnum() for c in w))
    return cjk_count + word_count


class CallMetricsCollector:
    """Accumulates all metrics for a single call session."""

    def __init__(
        self,
        call_uuid: str,
        robot_code: str,
        task_start_time: datetime.datetime,
        asr_info: dict,
        llm_info: dict,
        tts_info: dict,
        *,
        bot_mode: str,
        robot_version_id: Optional[str] = None,
        pricing_config: Optional[dict] = None,
    ):
        self.call_uuid = call_uuid
        self.bot_mode = bot_mode
        self.robot_code = robot_code
        self.robot_version_id = robot_version_id or None
        self.task_start_time = task_start_time
        self.task_end_time: Optional[datetime.datetime] = None

        def technical_info(info):
            return {
                key: value
                for key, value in info.items()
                if key in {"provider", "model", "locale", "sample_rate"}
            }

        # ASR fields
        self.asr_model_name: str = asr_info.get("model", "")
        self.asr_param_config: dict = technical_info(asr_info)
        self.asr_ttfb_list: list = []            # ms per turn
        self.asr_total_transcribed_chars: int = 0
        self.asr_total_audio_duration: float = 0.0   # seconds of user audio sent to STT
        self.asr_reconnect_count: int = 0

        # LLM fields
        self.llm_model_name: str = llm_info.get("model", "")
        self.llm_retry_count: int = 0
        self.llm_dialogue_turns: int = 0
        self.llm_ttfb_list: list = []            # ms per turn
        self.llm_total_generated_chars: int = 0

        # TTS fields
        self.tts_model_name: str = tts_info.get("model", "")
        self.tts_param_config: dict = technical_info(tts_info)
        self.tts_ttfb_list: list = []            # ms per turn
        self.tts_total_output_duration: float = 0.0   # seconds
        self.tts_total_chars: int = 0            # actual chars sent to TTS (for cost calc)
        self.tts_reconnect_count: int = 0

        self.technical_config = {
            "asr": self.asr_param_config,
            "llm": technical_info(llm_info),
            "tts": self.tts_param_config,
        }

        self._prompt_tokens: int = 0
        self._completion_tokens: int = 0
        self._cache_read_tokens: int = 0
        self._pricing = {**_DEFAULT_PRICING, **(pricing_config or {})}

        self.task_duration_sec: float = 0.0
        self.asr_cost: float = 0.0
        self.llm_cost: float = 0.0
        self.tts_cost: float = 0.0
        self.total_cost: float = 0.0

        self.intent_label: str = ""
        self.is_voicemail: int = 0
        self.forced_response_ttfb_breakdown: list[dict] = []

    # --- Increment APIs for reconnect/retry tracking ---

    def increment_asr_reconnect(self):
        """Call when ASR service reconnects."""
        self.asr_reconnect_count += 1

    def increment_tts_reconnect(self):
        """Call when TTS service reconnects."""
        self.tts_reconnect_count += 1

    def increment_llm_retry(self):
        """Call when LLM request is retried."""
        self.llm_retry_count += 1

    def set_intent_label(self, intent_label: Optional[str]):
        try:
            label = str(intent_label or "").strip().upper()
            self.intent_label = label
            self.is_voicemail = 1 if label == "G" else 0
        except Exception:
            pass

    # --- Finalization ---

    def finalize(self, task_end_time: datetime.datetime):
        """Calculate derived fields. Call once at call end. Idempotent — second call is a no-op."""
        if self.task_end_time is not None:
            return
        self.task_end_time = task_end_time
        delta = (task_end_time - self.task_start_time).total_seconds()
        self.task_duration_sec = round(max(0.0, delta), 2)

        p = self._pricing

        if self.asr_param_config.get("provider") == "cartesia":
            credits_per_second = p.get("cartesia_stt_credits_per_second", 1.0)
            credit_usd = p.get("cartesia_credit_usd", _CARTESIA_CREDIT_USD)
            credits = self.asr_total_audio_duration * credits_per_second
            self.asr_cost = round(credits * credit_usd, 8)
            self.asr_param_config["cartesia_stt_usage"] = {
                "billing_mode": "credits_per_audio_second",
                "audio_seconds": round(self.asr_total_audio_duration, 2),
                "credits_per_second": credits_per_second,
                "credits": round(credits, 4),
                "credit_usd": credit_usd,
                "cost": self.asr_cost,
            }
        else:
            # Deepgram: user audio duration (hours) x per-hour model rate.
            asr_model_key = f"asr_{self.asr_model_name.lower()}"
            asr_rate = p.get(asr_model_key) or p.get("asr_rate_per_hour", 0)
            self.asr_cost = round(
                self.asr_total_audio_duration / 3600.0 * asr_rate,
                8,
            )

        # LLM cost: token-based, keyed by "llm_<model-name-lowercase>_prompt/completion_per_1k"
        # Falls back to global "llm_prompt_per_1k" / "llm_completion_per_1k".
        llm_model_key = f"llm_{self.llm_model_name.lower()}"
        prompt_rate = p.get(f"{llm_model_key}_prompt_per_1k") or p.get("llm_prompt_per_1k", 0)
        completion_rate = p.get(f"{llm_model_key}_completion_per_1k") or p.get("llm_completion_per_1k", 0)
        cache_rate = p.get(f"{llm_model_key}_cache_read_per_1k") or p.get("llm_cache_read_per_1k", 0)
        self.llm_cost = round(
            self._prompt_tokens / 1000.0 * prompt_rate
            + self._completion_tokens / 1000.0 * completion_rate
            + self._cache_read_tokens / 1000.0 * cache_rate,
            8,
        )

        # TTS cost: actual characters sent to TTS
        self.tts_cost = round(self.tts_total_chars * p.get("tts_per_char", 0), 8)

        self.total_cost = round(self.asr_cost + self.llm_cost + self.tts_cost, 8)

    def record_forced_response_ttfb(
        self,
        *,
        reason: str,
        queue_wait_ms: Optional[float],
        model_ttfb_ms: Optional[float],
        end_to_end_ttfb_ms: Optional[float],
    ):
        self.forced_response_ttfb_breakdown.append(
            {
                "reason": reason,
                "queue_wait_ms": round(queue_wait_ms, 1)
                if queue_wait_ms is not None
                else None,
                "model_ttfb_ms": round(model_ttfb_ms, 1)
                if model_ttfb_ms is not None
                else None,
                "end_to_end_ttfb_ms": round(end_to_end_ttfb_ms, 1)
                if end_to_end_ttfb_ms is not None
                else None,
            }
        )


    def to_dict(self) -> dict:
        """Return all four tables' fields as a flat dict."""
        return {
            # Call basic
            "call_uuid": self.call_uuid,
            "task_duration_sec": self.task_duration_sec,
            "asr_cost": self.asr_cost,
            "llm_cost": self.llm_cost,
            "tts_cost": self.tts_cost,
            "total_cost": self.total_cost,
            "task_start_time": int(self.task_start_time.timestamp() * 1000) if self.task_start_time else None,
            "task_end_time": int(self.task_end_time.timestamp() * 1000) if self.task_end_time else None,
            "bot_mode": self.bot_mode,
            "robot_code": self.robot_code,
            "robot_version_id": self.robot_version_id,
            "is_voicemail": self.is_voicemail,
            "technical_config": self.technical_config,
            # ASR
            "asr_model_name": self.asr_model_name,
            "asr_param_config": self.asr_param_config,
            "asr_ttfb_list": self.asr_ttfb_list,
            "asr_total_transcribed_chars": self.asr_total_transcribed_chars,
            "asr_total_audio_duration": round(self.asr_total_audio_duration, 2),
            "asr_reconnect_count": self.asr_reconnect_count,
            "asr_connection_requests": self.asr_reconnect_count + 1,
            # LLM
            "llm_model_name": self.llm_model_name,
            "llm_retry_count": self.llm_retry_count,
            "llm_dialogue_turns": self.llm_dialogue_turns,
            "llm_ttfb_list": self.llm_ttfb_list,
            "forced_response_ttfb_breakdown": self.forced_response_ttfb_breakdown,
            "llm_total_generated_chars": self.llm_total_generated_chars,
            "llm_prompt_tokens": self._prompt_tokens,
            "llm_completion_tokens": self._completion_tokens,
            "llm_cache_read_tokens": self._cache_read_tokens,
            # TTS
            "tts_model_name": self.tts_model_name,
            "tts_param_config": self.tts_param_config,
            "tts_ttfb_list": self.tts_ttfb_list,
            "tts_total_output_duration": round(self.tts_total_output_duration, 2),
            "tts_total_chars": self.tts_total_chars,
            "tts_reconnect_count": self.tts_reconnect_count,
            "tts_connection_requests": self.tts_reconnect_count + 1,
        }

    def save_to_db(self):
        """Insert technical metrics into process_record after finalization."""
        try:
            d = self.to_dict()
            json_fields = [
                "technical_config", "asr_param_config", "asr_ttfb_list",
                "llm_ttfb_list", "tts_param_config", "tts_ttfb_list",
            ]
            for field in json_fields:
                if d.get(field) is not None:
                    d[field] = json.dumps(d[field], ensure_ascii=False)

            sql = text("""
                INSERT INTO process_record (
                    call_uuid, bot_mode, robot_code, robot_version_id,
                    task_duration_sec, task_start_time, task_end_time,
                    is_voicemail, technical_config,
                    asr_cost, llm_cost, tts_cost, total_cost,
                    asr_model_name, asr_param_config, asr_ttfb_list,
                    asr_total_transcribed_chars, asr_total_audio_duration,
                    asr_reconnect_count, asr_connection_requests,
                    llm_model_name, llm_retry_count, llm_dialogue_turns,
                    llm_ttfb_list, llm_total_generated_chars, llm_prompt_tokens,
                    llm_completion_tokens, llm_cache_read_tokens,
                    tts_model_name, tts_param_config, tts_ttfb_list,
                    tts_total_output_duration, tts_total_chars,
                    tts_reconnect_count, tts_connection_requests
                ) VALUES (
                    :call_uuid, :bot_mode, :robot_code, :robot_version_id,
                    :task_duration_sec, :task_start_time, :task_end_time,
                    :is_voicemail, :technical_config,
                    :asr_cost, :llm_cost, :tts_cost, :total_cost,
                    :asr_model_name, :asr_param_config, :asr_ttfb_list,
                    :asr_total_transcribed_chars, :asr_total_audio_duration,
                    :asr_reconnect_count, :asr_connection_requests,
                    :llm_model_name, :llm_retry_count, :llm_dialogue_turns,
                    :llm_ttfb_list, :llm_total_generated_chars, :llm_prompt_tokens,
                    :llm_completion_tokens, :llm_cache_read_tokens,
                    :tts_model_name, :tts_param_config, :tts_ttfb_list,
                    :tts_total_output_duration, :tts_total_chars,
                    :tts_reconnect_count, :tts_connection_requests
                )
            """)
            MysqlClient.execute(sql, d)
            logging.info(f"process_record inserted for call_uuid={self.call_uuid}")
        except Exception as e:
            logging.error(f"save_to_db failed for call_uuid={self.call_uuid}: {e}")


class CallMetricsObserver(BaseObserver):
    """Pipecat pipeline observer that feeds MetricsFrame data into CallMetricsCollector.

    Deduplication strategy
    ----------------------
    pipecat calls on_push_frame once per processor-to-processor hop.  A single
    frame emitted by service X flows through every downstream processor, so
    without dedup we'd count the same value N times.

    * MetricsFrame   – only process when data.source.name == metric.processor,
                       i.e. the frame is being pushed by its originating processor
                       (first hop). As it flows downstream, source.name changes.
    * TranscriptionFrame / AudioRawFrame – filtered by data.source class name so
      we only act when the originating service (Deepgram / Cartesia) pushes it.
    """

    def __init__(self, collector: CallMetricsCollector):
        super().__init__()
        self._c = collector
        # Track ASR TTFB independently: VADUserStartedSpeakingFrame → first transcript.
        # Pipecat's built-in TTFB measures VAD-stop → final transcript, which is always
        # negative for streaming STT (transcript arrives before user stops speaking).
        self._asr_speech_start_time: float = 0.0

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        source_name = data.source.name if data.source else ""
        source_cls = type(data.source).__name__ if data.source else ""

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._asr_speech_start_time = time.time()

        elif isinstance(frame, MetricsFrame):
            self._handle_metrics_frame(frame, source_name)

        elif isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            # InterimTranscriptionFrame / TranscriptionFrame are subclasses of TextFrame
            # and must be matched before the TextFrame branch below.
            # Only act when pushed directly by the STT service (first hop).
            if "Deepgram" in source_cls or "STT" in source_cls:
                # ASR TTFB: VAD-start → first transcript (interim or final).
                # Measured here rather than relying on pipecat's built-in TTFB metric
                # because streaming STT services (Deepgram, Cartesia ink-whisper) return
                # transcripts while the user is still speaking, making the pipecat
                # VAD-stop-based TTFB always negative and filtered.
                if self._asr_speech_start_time > 0:
                    ttfb_ms = round((time.time() - self._asr_speech_start_time) * 1000, 1)
                    self._c.asr_ttfb_list.append(ttfb_ms)
                    self._asr_speech_start_time = 0.0  # report once per utterance

                if isinstance(frame, TranscriptionFrame) and frame.text:
                    self._c.asr_total_transcribed_chars += _count_meaningful_units(frame.text)

        elif isinstance(frame, TextFrame):
            # Count LLM-generated chars (excluding spaces and punctuation) by
            # intercepting text frames arriving at the TTS service.
            # Using destination filter ensures we count each chunk exactly once.
            dest_cls = type(data.destination).__name__ if data.destination else ""
            if ("Cartesia" in dest_cls or "TTS" in dest_cls) and frame.text:
                self._c.llm_total_generated_chars += _count_meaningful_units(frame.text)

        elif isinstance(frame, InputAudioRawFrame):
            # Track user audio duration sent to STT service (for ASR cost calculation).
            # InputAudioRawFrame is a subclass of AudioRawFrame — must be matched first.
            dest_cls = type(data.destination).__name__ if data.destination else ""
            if ("Deepgram" in dest_cls or ("STT" in dest_cls and "Processor" not in dest_cls)) and frame.sample_rate > 0:
                # For Deepgram: only count when the WS connection is active.
                # After _disconnect(), _connection is set to None and audio is no longer
                # forwarded to Deepgram, so billing stops there.
                if "Deepgram" in dest_cls and getattr(data.destination, "_connection", None) is None:
                    pass
                else:
                    self._c.asr_total_audio_duration += frame.num_frames / frame.sample_rate

        elif isinstance(frame, AudioRawFrame):
            # Only count when pushed directly by the TTS service (first hop).
            if "Cartesia" in source_cls or ("TTS" in source_cls and "STT" not in source_cls):
                if frame.sample_rate > 0:
                    self._c.tts_total_output_duration += frame.num_frames / frame.sample_rate

        elif isinstance(frame, ErrorFrame):
            pass

    def _handle_metrics_frame(self, frame: MetricsFrame, source_name: str):
        for metric in frame.data:
            # Only process metrics whose originating processor matches the current
            # source. This ensures we handle each metric exactly once (on first hop).
            if metric.processor != source_name:
                continue
            if isinstance(metric, TTFBMetricsData):
                self._route_ttfb(metric)
            elif isinstance(metric, LLMUsageMetricsData):
                self._c.llm_dialogue_turns += 1
                self._c._prompt_tokens += metric.value.prompt_tokens
                self._c._completion_tokens += metric.value.completion_tokens
                self._c._cache_read_tokens += metric.value.cache_read_input_tokens or 0
            elif isinstance(metric, TTSUsageMetricsData):
                self._c.tts_total_chars += metric.value

    def _route_ttfb(self, metric: TTFBMetricsData):
        """Route TTFB metric to the correct list by processor name.

        ASR TTFB is tracked separately in on_push_frame (VAD-start → first transcript)
        and is NOT routed here, because pipecat's VAD-stop-based TTFB is always
        negative for streaming STT services and would be filtered or wrong.
        """
        if metric.value <= 0:
            return
        proc = metric.processor.lower()
        ttfb_ms = round(metric.value * 1000, 1)

        # STT services (CartesiaSTTService / DeepgramSTTService) contain "tts"
        # as a substring and would otherwise be misrouted into the TTS list.
        if "stt" in proc:
            return
        if "tts" in proc:
            self._c.tts_ttfb_list.append(ttfb_ms)
        elif "openai" in proc or "llm" in proc or "gpt" in proc:
            self._c.llm_ttfb_list.append(ttfb_ms)


# =====================================================================
# Realtime (gpt-realtime-mini) 计费的子类实现，自 realtime_collector 合并
# =====================================================================

import os

class RealtimeMiniCallMetricsCollector(CallMetricsCollector):
    """Call metrics collector for gpt-realtime-mini audio-to-audio billing."""

    _DISCOUNT = 0.77

    _TEXT_INPUT_PER_1K = 0.00060 * _DISCOUNT
    _TEXT_CACHED_INPUT_PER_1K = 0.00006 * _DISCOUNT
    _TEXT_OUTPUT_PER_1K = 0.00240 * _DISCOUNT
    _AUDIO_INPUT_PER_1K = 0.01000 * _DISCOUNT
    _AUDIO_CACHED_INPUT_PER_1K = 0.00030 * _DISCOUNT
    _AUDIO_OUTPUT_PER_1K = 0.02000 * _DISCOUNT
    _WHISPER_TRANSCRIPTION_PER_MIN = 0.00600 * _DISCOUNT
    _DEFAULT_AUDIO_TOKENS_PER_SECOND = 50.0
    # Azure Retail Prices API lists these as USD / 1M tokens for Global.
    # Internally the collector stores discounted token prices as USD / 1K tokens.
    _GPT_4O_MINI_TRANSCRIBE_AUDIO_INPUT_PER_1K = 3.00 / 1000.0 * _DISCOUNT
    _GPT_4O_MINI_TRANSCRIBE_TEXT_INPUT_PER_1K = 1.25 / 1000.0 * _DISCOUNT
    _GPT_4O_MINI_TRANSCRIBE_TEXT_OUTPUT_PER_1K = 5.00 / 1000.0 * _DISCOUNT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._realtime_mini_usage_count = 0
        self._realtime_mini_input_text_tokens = 0
        self._realtime_mini_input_audio_tokens = 0
        self._realtime_mini_output_text_tokens = 0
        self._realtime_mini_output_audio_tokens = 0
        self._realtime_mini_cached_text_tokens = 0
        self._realtime_mini_cached_audio_tokens = 0
        self._realtime_mini_cost_breakdown = {}
        self._realtime_transcription_usage_count = 0
        self._realtime_transcription_usage_type = ""
        self._realtime_transcription_total_tokens = 0
        self._realtime_transcription_input_tokens = 0
        self._realtime_transcription_output_tokens = 0
        self._realtime_transcription_input_text_tokens = 0
        self._realtime_transcription_input_audio_tokens = 0

    def add_realtime_token_usage(self, usage):
        if not usage:
            return

        input_details = self._value_at(usage, "input_token_details")
        output_details = self._value_at(usage, "output_token_details")
        cached_details = self._value_at(input_details, "cached_tokens_details")

        self._realtime_mini_usage_count += 1
        self._realtime_mini_input_text_tokens += self._int_at(
            input_details, "text_tokens"
        )
        self._realtime_mini_input_audio_tokens += self._int_at(
            input_details, "audio_tokens"
        )

        self._realtime_mini_output_text_tokens += self._int_at(
            output_details, "text_tokens"
        )
        self._realtime_mini_output_audio_tokens += self._int_at(
            output_details, "audio_tokens"
        )
        self._realtime_mini_cached_text_tokens += self._int_at(
            cached_details, "text_tokens"
        )
        self._realtime_mini_cached_audio_tokens += self._int_at(
            cached_details, "audio_tokens"
        )
        self._sync_realtime_mini_usage()

    def add_realtime_transcription_usage(self, usage):
        if not usage:
            return

        input_details = self._value_at(usage, "input_token_details") or {}

        input_tokens = self._int_at(usage, "input_tokens")
        text_tokens = self._int_at(input_details, "text_tokens")
        audio_tokens = self._int_at(input_details, "audio_tokens")
        output_tokens = self._int_at(usage, "output_tokens")
        total_tokens = self._int_at(usage, "total_tokens")

        if not total_tokens:
            total_tokens = input_tokens + output_tokens
        if input_tokens and not text_tokens and not audio_tokens:
            audio_tokens = input_tokens

        self._realtime_transcription_usage_count += 1
        self._realtime_transcription_usage_type = str(
            self._value_at(usage, "type", "") or ""
        )
        self._realtime_transcription_total_tokens += total_tokens
        self._realtime_transcription_input_tokens += input_tokens
        self._realtime_transcription_output_tokens += output_tokens
        self._realtime_transcription_input_text_tokens += text_tokens
        self._realtime_transcription_input_audio_tokens += audio_tokens
        self._sync_realtime_transcription_usage_to_asr_param()

    def finalize(self, task_end_time):
        super().finalize(task_end_time)
        self._finalize_realtime_transcription_cost()
        if not self._realtime_mini_usage_count:
            self.total_cost = round(self.asr_cost + self.llm_cost + self.tts_cost, 8)
            return

        self.llm_cost = round(self._calculate_realtime_mini_llm_cost(), 8)
        self.tts_cost = 0.0
        self.total_cost = round(self.asr_cost + self.llm_cost + self.tts_cost, 8)
        self._sync_realtime_mini_usage()

    def _finalize_realtime_transcription_cost(self):
        model = (self.asr_model_name or "").lower()
        if model == "whisper-1":
            self._finalize_whisper_transcription_cost()
            return
        if "transcribe" in model:
            self._finalize_gpt_transcription_cost(model)

    def _finalize_whisper_transcription_cost(self):
        price_per_min = self._float_env(
            "AZURE_WHISPER_TRANSCRIPTION_PER_MIN",
            self._WHISPER_TRANSCRIPTION_PER_MIN,
        )
        self.asr_cost = round(
            self.asr_total_audio_duration / 60.0 * price_per_min,
            8,
        )
        self.asr_param_config["realtime_transcription_cost"] = {
            "billing_mode": "per_minute",
            "model": self.asr_model_name,
            "discount": self._DISCOUNT,
            "audio_duration_sec": round(self.asr_total_audio_duration, 2),
            "price_per_min": price_per_min,
            "cost": self.asr_cost,
        }

    def _finalize_gpt_transcription_cost(self, model: str):
        default_prices = self._default_transcription_prices_per_1k(model)
        has_usage = self._realtime_transcription_usage_count > 0
        if has_usage:
            billing_mode = "azure_usage_tokens"
            audio_tokens = self._realtime_transcription_input_audio_tokens
            text_input_tokens = self._realtime_transcription_input_text_tokens
            output_text_tokens = self._realtime_transcription_output_tokens
        else:
            billing_mode = "audio_text_tokens_estimated"
            audio_tokens = self._estimate_audio_input_tokens()
            text_input_tokens = 0
            output_text_tokens = self._estimate_transcription_output_tokens()

        audio_input_per_1k = default_prices["audio_input_per_1k"]
        text_input_per_1k = default_prices["text_input_per_1k"]
        text_output_per_1k = default_prices["text_output_per_1k"]

        audio_input_cost = audio_tokens / 1000.0 * audio_input_per_1k
        text_input_cost = text_input_tokens / 1000.0 * text_input_per_1k
        text_output_cost = output_text_tokens / 1000.0 * text_output_per_1k

        total = audio_input_cost + text_input_cost + text_output_cost
        self.asr_cost = round(total, 8)
        self.asr_param_config["realtime_transcription_cost"] = {
            "billing_mode": billing_mode,
            "model": self.asr_model_name,
            "discount": self._DISCOUNT,
            "audio_duration_sec": round(self.asr_total_audio_duration, 2),
            "usage_count": self._realtime_transcription_usage_count,
            "usage_type": self._realtime_transcription_usage_type,
            "total_tokens": self._realtime_transcription_total_tokens,
            "input_tokens": self._realtime_transcription_input_tokens,
            "audio_input_tokens": audio_tokens,
            "text_input_tokens": text_input_tokens,
            "text_output_tokens": output_text_tokens,
            "audio_input_tokens_estimated": None if has_usage else audio_tokens,
            "text_input_tokens_estimated": None if has_usage else text_input_tokens,
            "text_output_tokens_estimated": None if has_usage else output_text_tokens,
            "pricing_source": default_prices["pricing_source"],
            "audio_input_per_1k": audio_input_per_1k,
            "text_input_per_1k": text_input_per_1k,
            "text_output_per_1k": text_output_per_1k,
            "audio_input_cost": round(audio_input_cost, 10),
            "text_input_cost": round(text_input_cost, 10),
            "text_output_cost": round(text_output_cost, 10),
            "cost": self.asr_cost,
        }

    def _estimate_audio_input_tokens(self) -> int:
        tokens_per_second = self._float_env(
            "AZURE_TRANSCRIBE_AUDIO_TOKENS_PER_SECOND",
            self._DEFAULT_AUDIO_TOKENS_PER_SECOND,
        )
        return int(round(max(self.asr_total_audio_duration, 0.0) * tokens_per_second))

    def _estimate_transcription_output_tokens(self) -> int:
        # We only have transcript text, not tokenizer output. Meaningful-unit count
        # is a conservative stable proxy across CJK and whitespace languages.
        return int(max(self.asr_total_transcribed_chars, 0))

    @classmethod
    def _default_transcription_prices_per_1k(cls, model: str) -> dict:
        normalized = model.upper().replace("-", "_").replace(".", "_")
        if "GPT_4O_MINI_TRANSCRIBE" in normalized:
            return {
                "pricing_source": "azure_retail_global_discounted_hardcoded",
                "audio_input_per_1k": cls._GPT_4O_MINI_TRANSCRIBE_AUDIO_INPUT_PER_1K,
                "text_input_per_1k": cls._GPT_4O_MINI_TRANSCRIBE_TEXT_INPUT_PER_1K,
                "text_output_per_1k": cls._GPT_4O_MINI_TRANSCRIBE_TEXT_OUTPUT_PER_1K,
            }
        return {
            "pricing_source": "unknown_transcription_model_hardcoded_zero",
            "audio_input_per_1k": 0.0,
            "text_input_per_1k": 0.0,
            "text_output_per_1k": 0.0,
        }

    @staticmethod
    def _float_env(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, default))
        except (TypeError, ValueError):
            return default

    def _calculate_realtime_mini_llm_cost(self):
        input_text_tokens = max(
            self._realtime_mini_input_text_tokens
            - self._realtime_mini_cached_text_tokens,
            0,
        )
        input_audio_tokens = max(
            self._realtime_mini_input_audio_tokens
            - self._realtime_mini_cached_audio_tokens,
            0,
        )

        input_text_cost = input_text_tokens / 1000.0 * self._TEXT_INPUT_PER_1K
        cached_text_cost = (
            self._realtime_mini_cached_text_tokens
            / 1000.0
            * self._TEXT_CACHED_INPUT_PER_1K
        )
        output_text_cost = (
            self._realtime_mini_output_text_tokens
            / 1000.0
            * self._TEXT_OUTPUT_PER_1K
        )
        input_audio_cost = input_audio_tokens / 1000.0 * self._AUDIO_INPUT_PER_1K
        cached_audio_cost = (
            self._realtime_mini_cached_audio_tokens
            / 1000.0
            * self._AUDIO_CACHED_INPUT_PER_1K
        )
        output_audio_cost = (
            self._realtime_mini_output_audio_tokens
            / 1000.0
            * self._AUDIO_OUTPUT_PER_1K
        )

        total = (
            input_text_cost
            + cached_text_cost
            + output_text_cost
            + input_audio_cost
            + cached_audio_cost
            + output_audio_cost
        )
        self._realtime_mini_cost_breakdown = {
            "discount": self._DISCOUNT,
            "input_text_cost": round(input_text_cost, 10),
            "cached_text_cost": round(cached_text_cost, 10),
            "output_text_cost": round(output_text_cost, 10),
            "input_audio_cost": round(input_audio_cost, 10),
            "cached_audio_cost": round(cached_audio_cost, 10),
            "output_audio_cost": round(output_audio_cost, 10),
            "total_cost": round(total, 10),
        }
        return total

    def _sync_realtime_mini_usage(self):
        self.technical_config["llm"]["realtime_mini_usage"] = {
            "discount": self._DISCOUNT,
            "usage_count": self._realtime_mini_usage_count,
            "input_text_tokens": self._realtime_mini_input_text_tokens,
            "input_audio_tokens": self._realtime_mini_input_audio_tokens,
            "output_text_tokens": self._realtime_mini_output_text_tokens,
            "output_audio_tokens": self._realtime_mini_output_audio_tokens,
            "cached_text_tokens": self._realtime_mini_cached_text_tokens,
            "cached_audio_tokens": self._realtime_mini_cached_audio_tokens,
            "cost_breakdown": self._realtime_mini_cost_breakdown,
        }

    def _sync_realtime_transcription_usage_to_asr_param(self):
        self.asr_param_config["realtime_transcription_usage"] = {
            "usage_count": self._realtime_transcription_usage_count,
            "usage_type": self._realtime_transcription_usage_type,
            "total_tokens": self._realtime_transcription_total_tokens,
            "input_tokens": self._realtime_transcription_input_tokens,
            "output_tokens": self._realtime_transcription_output_tokens,
            "input_text_tokens": self._realtime_transcription_input_text_tokens,
            "input_audio_tokens": self._realtime_transcription_input_audio_tokens,
        }

    @staticmethod
    def _value_at(value, key, default=None):
        if value is None:
            return default
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    @classmethod
    def _int_at(cls, value, key):
        try:
            return int(cls._value_at(value, key, 0) or 0)
        except (TypeError, ValueError):
            return 0
