#
# AI Copilot websocket protocol adapter for official Pipecat FastAPI transport.
# Keeps customization in serializer/router only.
#

import audioop
import base64
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
    StartInterruptionFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.base_serializer import FrameSerializer


class EventType:
    ACTION_CREATE = "action.create"
    ACTION_ANSWER = "action.answer"
    ACTION_HANGUP = "action.hangup"
    ACTION_MANUAL_INTERVENTION = "action.manual_intervention"
    AUDIO = "audio"
    TEXT = "text"
    DEBUG = "debug"
    HEARTBEAT = "heartbeat"


class Role:
    CUSTOMER = "CUSTOMER"
    AGENT = "AGENT"
    ROBOT = "ROBOT"
    SYSTEM = "SYSTEM"


@dataclass
class AICopilotEventFrame(Frame):
    event_name: str
    payload: Dict[str, Any]
    role: Optional[str] = None
    text: Optional[str] = None
    audio: Optional[bytes] = None
    sample_rate: int = 8000
    num_channels: int = 1


@dataclass
class InnerEvent:
    event_name: str
    payload: Dict[str, Any]
    role: Optional[str] = None
    text: Optional[str] = None
    audio: Optional[bytes] = None
    sample_rate: int = 8000
    num_channels: int = 1


def create_copilot_event(
    event_type: str,
    *,
    call_uuid: str,
    task_uuid: str,
    business_id: str,
    timestamp_ms: Optional[int] = None,
    **payload,
) -> dict[str, Any]:
    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "call_uuid": call_uuid,
        "task_uuid": task_uuid,
        "business_id": business_id,
        "timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
    }
    event.update(payload)
    return event


def create_text_event(
    *,
    call_uuid: str,
    task_uuid: str,
    business_id: str,
    text: str,
    role: str = Role.ROBOT,
) -> dict[str, Any]:
    return create_copilot_event(
        EventType.TEXT,
        call_uuid=call_uuid,
        task_uuid=task_uuid,
        business_id=business_id,
        text={
            "role": role,
            "text": text,
            "message_id": str(uuid.uuid4()),
        },
    )


def create_heartbeat_event(
    *,
    call_uuid: str,
    task_uuid: str,
    business_id: str,
) -> dict[str, Any]:
    return create_copilot_event(
        EventType.HEARTBEAT,
        call_uuid=call_uuid,
        task_uuid=task_uuid,
        business_id=business_id,
    )


def create_hangup_event(
    *,
    call_uuid: str,
    task_uuid: str,
    business_id: str,
) -> dict[str, Any]:
    return create_copilot_event(
        EventType.ACTION_HANGUP,
        call_uuid=call_uuid,
        task_uuid=task_uuid,
        business_id=business_id,
    )


class AICopilotFrameSerializer(FrameSerializer):
    """Serializer for AI Copilot event-driven websocket protocol."""
    _DISALLOWED_BINARY_SIGNATURES = {
        b"\x89PNG\r\n\x1a\n": "png",
        b"\xff\xd8\xff": "jpeg",
        b"GIF87a": "gif",
        b"GIF89a": "gif",
        b"RIFF": "riff",
        b"OggS": "ogg",
        b"ID3": "mp3",
        b"%PDF": "pdf",
        b"PK\x03\x04": "zip",
    }

    def __init__(
        self,
        *,
        call_uuid: str,
        task_uuid: str,
        business_id: str,
        input_sample_rate: int = 8000,
        output_sample_rate: int = 8000,
        channels: int = 1,
    ):
        super().__init__()
        self._call_uuid = call_uuid
        self._task_uuid = task_uuid
        self._business_id = business_id
        self._input_sample_rate = input_sample_rate
        self._output_sample_rate = output_sample_rate
        self._input_channels = channels
        self._output_channels = channels
        self._filter_pass_count = 0
        self._filter_drop_count = 0
        self._filter_total_ms = 0.0
        self._filter_log_every = 200

    def _detect_binary_signature(self, payload: bytes) -> Optional[str]:
        head = payload[:12]
        for signature, media_type in self._DISALLOWED_BINARY_SIGNATURES.items():
            if head.startswith(signature):
                return media_type
        return None

    def _is_valid_pcm16_frame(self, payload: bytes) -> bool:
        bytes_per_sample = 2 * max(1, self._input_channels)
        return len(payload) > 0 and len(payload) % bytes_per_sample == 0

    def _record_filter_timing(self, elapsed_ms: float, *, passed: bool, payload_size: int = 0) -> None:
        self._filter_total_ms += elapsed_ms
        if passed:
            self._filter_pass_count += 1
            should_log = (
                self._filter_pass_count % self._filter_log_every == 0
                or elapsed_ms >= 2.0
            )
            if should_log:
                avg_ms = self._filter_total_ms / max(1, self._filter_pass_count + self._filter_drop_count)
                logging.debug(
                    "AICopilotFrameSerializer: filter timing "
                    f"passed={self._filter_pass_count} dropped={self._filter_drop_count} "
                    f"last_ms={elapsed_ms:.3f} avg_ms={avg_ms:.3f} payload_size={payload_size}"
                )
            return

        self._filter_drop_count += 1

    def _binary_preview(self, payload: bytes, limit: int = 24) -> str:
        preview = payload[:limit].hex()
        digest = hashlib.sha1(payload).hexdigest()[:12]
        return f"sha1={digest} preview_hex={preview}"

    def _text_preview(self, payload: str, limit: int = 160) -> str:
        preview = payload[:limit].replace("\n", "\\n")
        digest = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"sha1={digest} preview={preview!r}"

    def _create_event(self, event_type: str, **kwargs) -> dict[str, Any]:
        return create_copilot_event(
            event_type,
            call_uuid=self._call_uuid,
            task_uuid=self._task_uuid,
            business_id=self._business_id,
            **kwargs,
        )

    async def deserialize(self, data: Union[str, bytes]) -> Optional[Frame]:
        if isinstance(data, (bytes, bytearray)):
            return None

        if not isinstance(data, str):
            return None

        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return None

        event_type = event.get("event_type")

        if event_type == EventType.AUDIO:
            audio_data = event.get("audio", {}) or {}
            role = audio_data.get("role")
            audio_base64 = audio_data.get("audio", "")

            if role in (Role.CUSTOMER, Role.AGENT) and audio_base64:
                filter_started_at = time.perf_counter()
                try:
                    audio_bytes = base64.b64decode(audio_base64, validate=True)
                except Exception:
                    elapsed_ms = (time.perf_counter() - filter_started_at) * 1000
                    self._record_filter_timing(elapsed_ms, passed=False, payload_size=len(audio_base64))
                    logging.warning(
                        "AICopilotFrameSerializer: dropped invalid base64 audio payload "
                        f"role={role}, filter_ms={elapsed_ms:.3f}, "
                        f"{self._text_preview(audio_base64)}"
                    )
                    return None

                media_type = self._detect_binary_signature(audio_bytes)
                if media_type:
                    elapsed_ms = (time.perf_counter() - filter_started_at) * 1000
                    self._record_filter_timing(elapsed_ms, passed=False, payload_size=len(audio_bytes))
                    logging.warning(
                        "AICopilotFrameSerializer: dropped non-PCM decoded payload "
                        f"signature={media_type}, size={len(audio_bytes)}, role={role}, "
                        f"filter_ms={elapsed_ms:.3f}, {self._binary_preview(audio_bytes)}"
                    )
                    return None

                if not self._is_valid_pcm16_frame(audio_bytes):
                    elapsed_ms = (time.perf_counter() - filter_started_at) * 1000
                    self._record_filter_timing(elapsed_ms, passed=False, payload_size=len(audio_bytes))
                    logging.warning(
                        "AICopilotFrameSerializer: dropped invalid PCM payload "
                        f"size={len(audio_bytes)}, channels={self._input_channels}, role={role}, "
                        f"filter_ms={elapsed_ms:.3f}, {self._binary_preview(audio_bytes)}"
                    )
                    return None
                elapsed_ms = (time.perf_counter() - filter_started_at) * 1000
                self._record_filter_timing(elapsed_ms, passed=True, payload_size=len(audio_bytes))

                return AICopilotEventFrame(
                    event_name="on_audio_received",
                    payload=event,
                    role=role,
                    audio=audio_bytes,
                    sample_rate=self._input_sample_rate,
                    num_channels=self._input_channels,
                )
            return None

        if event_type == EventType.TEXT:
            text_data = event.get("text", {}) or {}
            role = text_data.get("role")
            text = text_data.get("text", "")

            if role in (Role.CUSTOMER, Role.AGENT) and text:
                return AICopilotEventFrame(
                    event_name="on_customer_text",
                    payload=event,
                    role=role,
                    text=text,
                )
            return None

        event_mapping = {
            EventType.ACTION_CREATE: "on_session_create",
            EventType.ACTION_ANSWER: "on_answer",
            EventType.ACTION_MANUAL_INTERVENTION: "on_manual_intervention",
            EventType.HEARTBEAT: "on_heartbeat",
            EventType.ACTION_HANGUP: "on_hangup",
        }
        event_name = event_mapping.get(event_type)
        if event_name:
            payload = event.get("session_create", {}) if event_name == "on_session_create" else event
            return AICopilotEventFrame(event_name=event_name, payload=payload)

        return None

    async def serialize(self, frame: Frame) -> Optional[Union[bytes, str]]:
        if isinstance(frame, OutputAudioRawFrame):
            audio = frame.audio
            event = self._create_event(
                EventType.AUDIO,
                audio={
                    "role": Role.ROBOT,
                    "audio": base64.b64encode(audio).decode("utf-8"),
                },
            )
            return json.dumps(event, ensure_ascii=False)

        if isinstance(frame, TextFrame):
            event = self._create_event(
                EventType.TEXT,
                text={
                    "role": Role.ROBOT,
                    "text": frame.text,
                    "message_id": str(uuid.uuid4()),
                },
            )
            return json.dumps(event, ensure_ascii=False)

        if isinstance(frame, StartInterruptionFrame):
            event = self._create_event(
                EventType.DEBUG,
                debug={
                    "initiator": Role.SYSTEM,
                    "debug_type": "interruption",
                    "message": "User interrupted",
                },
            )
            return json.dumps(event, ensure_ascii=False)

        message = getattr(frame, "message", None)
        if isinstance(message, dict):
            return json.dumps(message, ensure_ascii=False)

        return None


AICopilotEventHandler = Callable[[AICopilotEventFrame], Awaitable[None]]
InnerEventHandler = Callable[[InnerEvent], Awaitable[None]]


class AICopilotEventRouter(FrameProcessor):
    """Routes websocket protocol events and forwards payload frames downstream."""

    def __init__(self, *, cancel_task_cb=None, forward_customer_text=False):
        super().__init__()
        self._handlers: Dict[str, List[AICopilotEventHandler]] = defaultdict(list)
        self._cancel_task_cb = cancel_task_cb
        self._forward_customer_text = forward_customer_text

    def on(self, event_name: str, handler: AICopilotEventHandler):
        self._handlers[event_name].append(handler)

    async def _dispatch_handlers(self, frame: AICopilotEventFrame):
        handlers = self._handlers.get(frame.event_name, [])
        if not handlers:
            logging.debug(
                "AICopilotEventRouter: unhandled event name=%s payload=%s",
                frame.event_name,
                frame.payload,
            )
            return

        for handler in handlers:
            await handler(frame)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not isinstance(frame, AICopilotEventFrame):
            await self.push_frame(frame, direction)
            return

        if frame.event_name == "on_hangup" and self._cancel_task_cb:
            await self._cancel_task_cb()

        await self._dispatch_handlers(frame)

        if frame.event_name == "on_audio_received" and frame.audio:
            await self.push_frame(
                InputAudioRawFrame(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                ),
                direction,
            )
            return

        if frame.event_name == "on_customer_text":
            if self._forward_customer_text and frame.text:
                await self.push_frame(
                    TranscriptionFrame(
                        text=frame.text,
                        user_id=frame.role or Role.CUSTOMER,
                        timestamp=str(int(time.time() * 1000)),
                        finalized=True,
                    ),
                    direction,
                )
            return


class InnerEventRouter:
    """Routes internal non-protocol events produced inside the pipeline."""

    def __init__(self):
        self._handlers: Dict[str, List[InnerEventHandler]] = defaultdict(list)

    def on(self, event_name: str, handler: InnerEventHandler):
        self._handlers[event_name].append(handler)

    async def emit(
        self,
        event_name: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        role: Optional[str] = None,
        text: Optional[str] = None,
        audio: Optional[bytes] = None,
        sample_rate: int = 8000,
        num_channels: int = 1,
    ):
        event = InnerEvent(
            event_name=event_name,
            payload=payload or {},
            role=role,
            text=text,
            audio=audio,
            sample_rate=sample_rate,
            num_channels=num_channels,
        )
        handlers = self._handlers.get(event_name, [])
        if not handlers:
            logging.debug(
                "InnerEventRouter: unhandled event name=%s payload=%s",
                event_name,
                event.payload,
            )
            return

        for handler in handlers:
            await handler(event)


class CopilotEventObserver(FrameProcessor):
    """Observes pipeline frames and emits internal events through InnerEventRouter."""

    SENTENCE_ENDINGS = frozenset("。！？.!?")

    def __init__(
        self,
        router: InnerEventRouter,
        *,
        emit_transcription: bool = False,
        emit_llm_text: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._router = router
        self._emit_transcription = emit_transcription
        self._emit_llm_text = emit_llm_text
        self._llm_text_buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if self._emit_transcription and isinstance(frame, TranscriptionFrame):
            await self._router.emit(
                "on_transcription",
                payload={"is_final": True},
                text=frame.text,
                role=frame.user_id,
            )

        if self._emit_llm_text:
            if isinstance(frame, LLMTextFrame):
                await self._consume_llm_text(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                await self._flush_llm_text()

        await self.push_frame(frame, direction)

    async def _consume_llm_text(self, text: str):
        clean = text.replace("LLMFullResponseEndFrame", "")
        if not clean:
            return

        self._llm_text_buffer += clean
        await self._emit_complete_sentences()

    async def _emit_complete_sentences(self):
        while self._llm_text_buffer:
            end_idx = -1
            for idx, ch in enumerate(self._llm_text_buffer):
                if ch in self.SENTENCE_ENDINGS:
                    end_idx = idx
                    break

            if end_idx == -1:
                return

            sentence = self._llm_text_buffer[: end_idx + 1].strip()
            self._llm_text_buffer = self._llm_text_buffer[end_idx + 1 :]
            if sentence:
                await self._router.emit(
                    "on_llm_text",
                    payload={},
                    role=Role.ROBOT,
                    text=sentence,
                )

    async def _flush_llm_text(self):
        remaining = self._llm_text_buffer.strip()
        self._llm_text_buffer = ""
        if remaining:
            await self._router.emit(
                "on_llm_text",
                payload={},
                role=Role.ROBOT,
                text=remaining,
            )
