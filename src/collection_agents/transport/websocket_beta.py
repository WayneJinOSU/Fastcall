from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union
import json
import audioop
import logging
from collections import defaultdict

_logger = logging.getLogger(__name__)

from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.frames.frames import (
    Frame, StartFrame,
    InputAudioRawFrame, AudioRawFrame, OutputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameProcessor


@dataclass
class TelephonyEventFrame(Frame):
    event_name: str
    call_id: Optional[str] = None
    payload: Dict[str, Any] = None
    ts: Optional[float] = None


class CustomProtocolFrameSerializer(FrameSerializer):
    _ACTION_TO_EVENT_NAME = {
        "start": "on_start",
        "ring": "on_ring",
        "answer": "on_answer",
        "dtmf": "on_dtmf",
        "stat": "on_stat",
        "talk": "on_talk",
        "hangup": "on_hangup",
    }

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        input_sample_rate: int = 8000,
        output_sample_rate: int = 8000,
    ):
        super().__init__()
        self.sample_rate = sample_rate
        self.channels = channels
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate

    def _normalize_event_name(self, raw_name: Optional[str]) -> str:
        if not raw_name:
            return ""
        name = str(raw_name).strip()
        if not name:
            return ""
        lowered = name.lower()
        if lowered in self._ACTION_TO_EVENT_NAME:
            return self._ACTION_TO_EVENT_NAME[lowered]
        if lowered in self._ACTION_TO_EVENT_NAME.values():
            return lowered
        if lowered.startswith("on_") and lowered[3:] in self._ACTION_TO_EVENT_NAME:
            return f"on_{lowered[3:]}"
        if "." in lowered:
            tail = lowered.split(".")[-1]
            if tail in self._ACTION_TO_EVENT_NAME:
                return self._ACTION_TO_EVENT_NAME[tail]
        return lowered

    async def setup(self, frame: StartFrame):
        audio_in_sample_rate = getattr(frame, "audio_in_sample_rate", None)
        audio_in_channels = getattr(frame, "audio_in_channels", None)

        if audio_in_sample_rate:
            self.sample_rate = audio_in_sample_rate
        if audio_in_channels:
            self.channels = audio_in_channels

    async def deserialize(self, data: Union[str, bytes]) -> Optional[Frame]:
        if isinstance(data, (bytes, bytearray)):
            audio = bytes(data)
            # if _logger.isEnabledFor(logging.DEBUG):
            #     preview = audio[:64]
            #     looks_like_base64 = all(0x20 <= b < 0x7F for b in preview) if preview else False
            #     _logger.debug(
            #         "deserialize audio: len=%d, first_bytes_hex=%s, looks_like_base64=%s",
            #         len(audio),
            #         preview.hex(),
            #         looks_like_base64,
            #     )
            # if len(audio) >= 16 and all(0x20 <= b < 0x7F for b in audio[:64]):
            #     _logger.warning(
            #         "deserialize: incoming audio bytes look like printable ASCII / base64! "
            #         "len=%d, preview=%r — check if client is sending base64-encoded audio as raw bytes",
            #         len(audio),
            #         audio[:64],
            #     )
            if self.input_sample_rate != self.sample_rate:
                audio = audioop.ratecv(
                    audio,
                    2,
                    self.channels,
                    self.input_sample_rate,
                    self.sample_rate,
                    None,
                )[0]
            return InputAudioRawFrame(
                audio=audio,
                sample_rate=self.sample_rate,
                num_channels=self.channels,
            )

        if isinstance(data, str):
            # _logger.debug("deserialize: received str message, len=%d, preview=%r", len(data), data[:200])
            try:
                obj = json.loads(data)
            except Exception:
                return None

            if obj.get("type") == "event":
                event_name = self._normalize_event_name(
                    obj.get("name") or obj.get("event") or obj.get("action")
                )
                return TelephonyEventFrame(
                    event_name=event_name,
                    call_id=obj.get("call_id") or obj.get("session_id"),
                    payload=obj.get("payload") or obj.get("data") or {},
                    ts=obj.get("ts"),
                )

            action = obj.get("action")
            if action:
                event_name = self._normalize_event_name(action)
                if event_name:
                    return TelephonyEventFrame(
                        event_name=event_name,
                        call_id=obj.get("call_id") or obj.get("session_id"),
                        payload=obj.get("data") or {},
                        ts=obj.get("ts"),
                    )
        return None

    async def serialize(self, frame: Frame) -> Optional[Union[bytes, str]]:
        if isinstance(frame, (AudioRawFrame, OutputAudioRawFrame)):
            audio = frame.audio
            frame_sample_rate = getattr(frame, "sample_rate", self.output_sample_rate)
            frame_channels = getattr(frame, "num_channels", self.channels)
            if frame_sample_rate != self.output_sample_rate:
                audio = audioop.ratecv(
                    audio,
                    2,
                    frame_channels,
                    frame_sample_rate,
                    self.output_sample_rate,
                    None,
                )[0]
            return audio
        return None


EventHandler = Callable[[TelephonyEventFrame], Awaitable[None]]


class TelephonyEventRouter(FrameProcessor):
    def __init__(self, *, cancel_task_cb=None):
        super().__init__()
        self._handlers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._cancel_task_cb = cancel_task_cb
        self.answered = False

    def on(self, event_name: str, handler: EventHandler):
        self._handlers[event_name].append(handler)

    async def process_frame(self, frame: Frame, direction):
        await super().process_frame(frame, direction)

        if not isinstance(frame, TelephonyEventFrame):
            await self.push_frame(frame, direction)
            return

        name = frame.event_name

        if name == "on_answer":
            self.answered = True
            logging.info(f"TelephonyEventRouter: received on_answer payload={frame.payload}")
        elif name == "on_hangup":
            if self._cancel_task_cb:
                await self._cancel_task_cb()

        handlers = self._handlers.get(name, [])
        if not handlers:
            logging.debug(f"TelephonyEventRouter: unhandled event name={name}, payload={frame.payload}")
        for h in handlers:
            await h(frame)
