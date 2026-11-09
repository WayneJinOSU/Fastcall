from pipecat.frames.frames import (
    AudioRawFrame,
    ErrorFrame,
    InputAudioRawFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed

from collection_agents.metrics.collector import CallMetricsCollector


class RealtimeCallMetricsObserver(BaseObserver):
    """Metrics observer for speech-to-speech Azure Realtime frames."""

    def __init__(self, collector: CallMetricsCollector):
        super().__init__()
        self._c = collector

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        source_cls = type(data.source).__name__ if data.source else ""
        dest_cls = type(data.destination).__name__ if data.destination else ""
        source_name = data.source.name if data.source else ""

        if isinstance(frame, MetricsFrame):
            self._handle_metrics_frame(frame, source_name)
        elif isinstance(frame, TranscriptionFrame):
            if "Realtime" in source_cls and frame.text:
                self._c.asr_total_transcribed_chars += self._count_units(frame.text)
        elif isinstance(frame, TTSTextFrame):
            if "Realtime" in source_cls and frame.text:
                units = self._count_units(frame.text)
                self._c.llm_total_generated_chars += units
                self._c.tts_total_chars += len(frame.text)
        elif isinstance(frame, InputAudioRawFrame):
            if "Realtime" in dest_cls and frame.sample_rate > 0:
                self._c.asr_total_audio_duration += frame.num_frames / frame.sample_rate
        elif isinstance(frame, AudioRawFrame):
            if "Realtime" in source_cls and frame.sample_rate > 0:
                self._c.tts_total_output_duration += frame.num_frames / frame.sample_rate
        elif isinstance(frame, ErrorFrame):
            pass

    def _handle_metrics_frame(self, frame: MetricsFrame, source_name: str):
        for metric in frame.data:
            if metric.processor != source_name:
                continue
            if isinstance(metric, TTFBMetricsData):
                if metric.value > 0:
                    self._c.llm_ttfb_list.append(round(metric.value * 1000, 1))
            elif isinstance(metric, LLMUsageMetricsData):
                self._c.llm_dialogue_turns += 1
                self._c._prompt_tokens += metric.value.prompt_tokens
                self._c._completion_tokens += metric.value.completion_tokens
                self._c._cache_read_tokens += metric.value.cache_read_input_tokens or 0

    @staticmethod
    def _count_units(text: str) -> int:
        count = 0
        in_word = False
        for char in text:
            if "\u3400" <= char <= "\u9fff":
                count += 1
                in_word = False
            elif char.isalnum():
                if not in_word:
                    count += 1
                    in_word = True
            else:
                in_word = False
        return count
