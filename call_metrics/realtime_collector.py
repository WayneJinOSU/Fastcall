import os

from call_metrics.collector import CallMetricsCollector


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
        self._sync_realtime_mini_usage_to_llm_param()

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
        self._sync_realtime_mini_usage_to_llm_param()

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

    def _sync_realtime_mini_usage_to_llm_param(self):
        self.llm_param["realtime_mini_usage"] = {
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
