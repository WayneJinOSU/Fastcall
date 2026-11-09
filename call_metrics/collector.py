"""
Call-level metrics collector and pipecat observer.

Usage in bot_beta.py:
    collector = CallMetricsCollector(
        call_uuid=call_uuid,
        start_time=datetime.datetime.now(),
        asr_info={"model": "nova-2", "language": "zh-CN", "sample_rate": 8000},
        llm_info={"model": "openai/gpt-4o", "temperature": 0, "max_tokens": 100},
        tts_info={"model": "sonic-2", "voice_id": "...", "sample_rate": 8000},
        llm_prompt="...",
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

from my_utils.mysql import MysqlClient
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
        business_id: str,
        tenant_id: str,
        app_type: str,
        work_order_id: str,
        platform: str,
        is_round_robin: int,
        is_human_assist:int,
        robot_code: str,
        robot_name: str,
        org_code: str,
        org_name: str,
        country: str,
        task_type: str,
        follower_id: str,
        dial_schedule: list,
        customer_group: str,
        has_sms: int,
        has_app_push: int,
        task_start_time: datetime.datetime,
        asr_info: dict,
        llm_info: dict,
        tts_info: dict,
        llm_prompt: str = "",
        pricing_config: Optional[dict] = None,
        outbound_source: str = "hermes",
        prompt_uuid: str = "",
    ):
        """
        Args:
            call_uuid: Unique call identifier.
            start_time: When the call started.
            asr_info: e.g. {"model": "nova-2", "language": "zh-CN", "sample_rate": 8000}
            llm_info: e.g. {"model": "openai/gpt-4o", "temperature": 0, "max_tokens": 100}
            tts_info: e.g. {"model": "sonic-2", "voice_id": "...", "sample_rate": 8000}
            llm_prompt: System prompt content (version record).
            pricing_config: Optional pricing for cost calculation, e.g.
                {
                    "llm_prompt_per_1k": 0.005,       # USD per 1k prompt tokens
                    "llm_completion_per_1k": 0.015,   # USD per 1k completion tokens
                    "tts_per_char": 0.000015,         # USD per character
                }
        """
        self.call_uuid = call_uuid
        self.task_start_time = task_start_time
        self.business_id = business_id
        self.tenant_id = tenant_id
        self.app_type = app_type
        self.work_order_id = work_order_id
        self.outbound_source = outbound_source or "hermes"
        self.platform = platform
        self.is_round_robin = is_round_robin
        self.is_human_assist = is_human_assist
        self.robot_code = robot_code
        self.robot_name = robot_name
        self.org_code = org_code
        self.org_name = org_name
        self.country = country
        self.task_type = task_type
        self.follower_id = follower_id
        self.dial_schedule = dial_schedule
        self.customer_group = customer_group
        self.has_sms = has_sms
        self.has_app_push = has_app_push
        self.task_end_time: Optional[datetime.datetime] = None

        # ASR fields
        self.asr_model_name: str = asr_info.get("model", "")
        self.asr_param_config: dict = asr_info
        self.asr_ttfb_list: list = []            # ms per turn
        self.asr_total_transcribed_chars: int = 0
        self.asr_total_audio_duration: float = 0.0   # seconds of user audio sent to STT
        self.asr_reconnect_count: int = 0

        # LLM fields
        # DeepSeek 和 Ali Qwen 使用实际运行模型名；Azure/openrouter 机器人的
        # model 配置仍是 "openai/gpt-4o" 占位符，因此取实际部署模型名。
        _llm_provider = llm_info.get("provider", "")
        if _llm_provider == "deepseek":
            self.llm_model_name: str = "deepseek-flash"
        elif _llm_provider == "ali_qwen":
            self.llm_model_name: str = llm_info.get("model") or "d-1_model"
        else:
            self.llm_model_name: str = "gpt-5.4-mini"
        self.llm_param: dict = llm_info
        self.llm_prompt: str = llm_prompt
        self.prompt_uuid: str = prompt_uuid or ""
        self.llm_retry_count: int = 0
        self.llm_dialogue_turns: int = 0
        self.llm_ttfb_list: list = []            # ms per turn
        self.llm_total_generated_chars: int = 0

        # TTS fields
        self.tts_model_name: str = tts_info.get("model", "")
        self.tts_param_config: dict = tts_info
        self.tts_ttfb_list: list = []            # ms per turn
        self.tts_total_output_duration: float = 0.0   # seconds
        self.tts_total_chars: int = 0            # actual chars sent to TTS (for cost calc)
        self.tts_reconnect_count: int = 0

        # Internal token accumulators (for cost calculation)
        self._prompt_tokens: int = 0
        self._completion_tokens: int = 0
        self._cache_read_tokens: int = 0
        self._pricing = {**_DEFAULT_PRICING, **(pricing_config or {})}

        # Derived (set by finalize)
        self.task_duration_sec: float = 0.0
        self.asr_cost: float = 0.0
        self.llm_cost: float = 0.0
        self.tts_cost: float = 0.0
        self.total_cost: float = 0.0

        # Intent/callback-derived fields
        self.intent_label: str = ""
        self.is_recovered: int = 0
        self.is_voicemail: int = 0
        self.forced_response_ttfb_breakdown: list[dict] = []
        self.collection_result: str = ""  # raw collectionResult from dify

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
            self.is_recovered = 1 if label == "A" else 0
            self.is_voicemail = 1 if label == "G" else 0
        except Exception:
            pass

    def set_dify_result(self, dify_result: dict, task_type: Optional[str] = None):
        """Extract collection_result from dify result.

        Args:
            dify_result: The parsed dify result dict, e.g.
                {"collectionResult": "yes_voicemail", "repaymentWish": "yes", ...}
            task_type: Reserved for backward compatibility.
        """
        try:
            if task_type != "marketing":
                self.collection_result = dify_result.get("collectionResult", "")
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
            # Business
            "business_id": self.business_id,
            "tenant_id": self.tenant_id,
            "app_type": self.app_type,
            "work_order_id": self.work_order_id,
            "outbound_source": self.outbound_source,
            "platform": self.platform,
            "is_round_robin": self.is_round_robin,
            "is_human_assist":self.is_human_assist,
            "is_recovered": self.is_recovered,
            "is_voicemail": self.is_voicemail,
            "collection_result": self.collection_result,
            "robot_code": self.robot_code,
            "robot_name": self.robot_name,
            "org_code": self.org_code,
            "org_name": self.org_name,
            "country": self.country,
            "task_type": self.task_type,
            "follower_id": self.follower_id,
            "dial_schedule": self.dial_schedule,
            "customer_group": self.customer_group,
            "has_sms": self.has_sms,
            "has_app_push": self.has_app_push,
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
            "llm_param": self.llm_param,
            "llm_prompt": self.llm_prompt,
            "prompt_uuid": self.prompt_uuid,
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
        """Insert metrics into process_record table. Must be called after finalize()."""
        try:
            d = self.to_dict()
            # Serialize list/dict fields to JSON strings
            json_fields = [
                "dial_schedule", "asr_param_config", "asr_ttfb_list",
                "llm_param", "llm_ttfb_list", "tts_param_config", "tts_ttfb_list",
            ]
            for f in json_fields:
                if d.get(f) is not None:
                    d[f] = json.dumps(d[f], ensure_ascii=False)

            sql = text("""
                INSERT INTO process_record (
                    call_uuid, task_duration_sec, task_start_time, task_end_time,
                    asr_cost, llm_cost, tts_cost, total_cost,
                    business_id, tenant_id, app_type, follower_id, dial_schedule,
                    customer_group, has_sms, has_app_push, work_order_id, outbound_source, platform,
                    is_round_robin, is_human_assist, is_recovered, is_voicemail,
                    collection_result, robot_code, robot_name, org_code, org_name,
                    country, task_type,
                    asr_model_name, asr_param_config, asr_ttfb_list,
                    asr_total_transcribed_chars, asr_total_audio_duration,
                    asr_reconnect_count, asr_connection_requests,
                    llm_model_name, llm_param, llm_prompt,
                    llm_retry_count, llm_dialogue_turns, llm_ttfb_list,
                    llm_total_generated_chars, llm_prompt_tokens,
                    llm_completion_tokens, llm_cache_read_tokens,
                    tts_model_name, tts_param_config, tts_ttfb_list,
                    tts_total_output_duration, tts_total_chars,
                    tts_reconnect_count, tts_connection_requests
                ) VALUES (
                    :call_uuid, :task_duration_sec, :task_start_time, :task_end_time,
                    :asr_cost, :llm_cost, :tts_cost, :total_cost,
                    :business_id, :tenant_id, :app_type, :follower_id, :dial_schedule,
                    :customer_group, :has_sms, :has_app_push, :work_order_id, :outbound_source, :platform,
                    :is_round_robin, :is_human_assist, :is_recovered, :is_voicemail,
                    :collection_result, :robot_code, :robot_name, :org_code, :org_name,
                    :country, :task_type,
                    :asr_model_name, :asr_param_config, :asr_ttfb_list,
                    :asr_total_transcribed_chars, :asr_total_audio_duration,
                    :asr_reconnect_count, :asr_connection_requests,
                    :llm_model_name, :llm_param, :llm_prompt,
                    :llm_retry_count, :llm_dialogue_turns, :llm_ttfb_list,
                    :llm_total_generated_chars, :llm_prompt_tokens,
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
