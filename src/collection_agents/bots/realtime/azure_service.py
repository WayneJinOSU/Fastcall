import json
import logging
import time
from typing import Optional

from pipecat.frames.frames import Frame, InputAudioRawFrame, LLMFullResponseStartFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.azure.realtime.llm import AzureRealtimeLLMService
from pipecat.services.openai.realtime.events import (
    ConversationItem,
    ConversationItemCreateEvent,
    ConversationItemDeleteEvent,
    InputAudioBufferCommitEvent,
    ItemContent,
    ResponseCancelEvent,
    ResponseCreateEvent,
    ResponseProperties,
)

from collection_agents.bots.realtime.utils import _content_to_text, _summarize_for_log
from collection_agents.metrics.collector import CallMetricsCollector


class RealtimePromptSyncAzureLLMService(AzureRealtimeLLMService):
    """Azure Realtime wrapper that can force responses for appended system prompts."""

    def __init__(
        self,
        *args,
        allow_interruptions: bool = False,
        realtime_voice: str = "",
        metrics_collector: Optional[CallMetricsCollector] = None,
        realtime_monitor_state: Optional[dict] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._server_vad_interruptions = allow_interruptions
        self._realtime_voice = realtime_voice
        self._metrics_collector = metrics_collector
        self._realtime_monitor_state = realtime_monitor_state
        self._session_voice_sent = False
        self._assistant_audio_started = False
        self._audio_resumed_after_session_update = False
        self._synced_context_message_count = 0
        self._pending_forced_response_instructions: list[dict] = []
        self._active_forced_response_entry: Optional[dict] = None
        self._response_create_in_flight = False
        self._response_setup_in_progress = False
        self._barge_in_cancel_pending = False
        self._create_response_after_cancel = False
        self._deleted_conversation_item_ids: set[str] = set()
        self._handled_barge_in_item_ids: set[str] = set()
        self._has_uncommitted_input_audio = False
        self._uncommitted_input_audio_ms = 0.0
        self._manual_input_audio_commit_sent = False
        if self._realtime_monitor_state is not None:
            self._realtime_monitor_state.setdefault("assistant_response_active", False)

    def _set_realtime_monitor_flag(self, key: str, value: bool):
        state = getattr(self, "_realtime_monitor_state", None)
        if state is not None:
            state[key] = value

    def _is_assistant_output_active(self) -> bool:
        state = getattr(self, "_realtime_monitor_state", None)
        return bool(
            state
            and (
                state.get("bot_speaking")
                or state.get("assistant_response_active")
            )
        )

    async def send_client_event(self, event):
        payload = event.model_dump(exclude_none=True)
        event_type = payload.get("type")
        if payload.get("type") == "session.update":
            turn_detection = (
                payload.get("session", {})
                .get("audio", {})
                .get("input", {})
                .get("turn_detection")
            )
            if isinstance(turn_detection, dict):
                turn_detection["create_response"] = False
                turn_detection["interrupt_response"] = self._server_vad_interruptions
                logging.info(
                    "Azure Realtime server_vad configured: "
                    "create_response=false, interrupt_response=%s",
                    self._server_vad_interruptions,
                )
            audio_output = (
                payload.get("session", {})
                .get("audio", {})
                .get("output", {})
            )
            if isinstance(audio_output, dict) and audio_output.get("voice"):
                response_active_or_pending = (
                    self._current_assistant_response
                    or self._response_create_in_flight
                    or self._response_setup_in_progress
                )
                if (
                    self._session_voice_sent
                    or response_active_or_pending
                    or self._assistant_audio_started
                ):
                    audio_output.pop("voice", None)
                    logging.info(
                        "Removed voice from follow-up Azure Realtime session.update."
                    )
                else:
                    self._session_voice_sent = True
        elif event_type == "response.create":
            if self._current_assistant_response or self._response_create_in_flight:
                self._set_realtime_monitor_flag("assistant_response_active", True)
                logging.info(
                    "Skip duplicate Azure Realtime response.create before send; "
                    "response already active or pending."
                )
                return
            self._remove_response_voice(payload)
            self._assistant_audio_started = True
            self._response_create_in_flight = True
            self._set_realtime_monitor_flag("assistant_response_active", True)
        await self._ws_send(payload)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame) and getattr(frame, "num_frames", 0) > 0:
            self._has_uncommitted_input_audio = True
            sample_rate = float(getattr(frame, "sample_rate", 0) or 0)
            if sample_rate > 0:
                self._uncommitted_input_audio_ms += (
                    float(frame.num_frames) / sample_rate
                ) * 1000.0

        await self.push_frame(frame, direction)

    def _mark_realtime_send_failed(self):
        self._api_session_ready = False
        self._response_create_in_flight = False
        self._response_setup_in_progress = False
        self._current_assistant_response = None
        self._current_audio_response = None
        self._barge_in_cancel_pending = False
        self._create_response_after_cancel = False
        self._set_realtime_monitor_flag("assistant_response_active", False)

    async def _ws_send(self, realtime_message):
        try:
            if self._disconnecting:
                return False
            if not self._websocket:
                self._mark_realtime_send_failed()
                await self.push_error(
                    error_msg="Error sending client event: websocket is not connected",
                    fatal=True,
                )
                return False

            await self._websocket.send(json.dumps(realtime_message))
            return True
        except Exception as exc:
            if self._disconnecting or not self._websocket:
                return False

            self._mark_realtime_send_failed()
            await self.push_error(
                error_msg=f"Error sending client event: {exc}",
                exception=exc,
                fatal=True,
            )
            return False

    @staticmethod
    def _remove_response_voice(payload: dict):
        response = payload.get("response")
        if not isinstance(response, dict):
            return
        audio = response.get("audio")
        if not isinstance(audio, dict):
            return
        output = audio.get("output")
        if not isinstance(output, dict):
            return
        if output.pop("voice", None):
            logging.info(
                "Removed voice from Azure Realtime response.create; "
                "session voice is already configured."
            )

    async def _handle_evt_speech_started(self, evt):
        self._mark_input_speech_started(evt)
        if self._server_vad_interruptions:
            await super()._handle_evt_speech_started(evt)
            return
        logging.debug(
            "Ignoring Azure Realtime speech_started interruption; VAD barge-in is disabled."
        )
        return

    def _mark_input_speech_started(self, evt):
        state = self._realtime_monitor_state
        if not state or not self._is_assistant_output_active():
            return
        item_id = str(getattr(evt, "item_id", "") or "")
        if not item_id:
            return
        state.setdefault("barge_in_item_ids", set()).add(item_id)
        state["barge_in_seen"] = True
        logging.info(
            "Realtime input speech started while bot speaking; "
            "item_id=%s will be monitored as barge-in ASR.",
            item_id,
        )

    async def _handle_evt_audio_delta(self, evt):
        self._set_realtime_monitor_flag("bot_speaking", True)
        await super()._handle_evt_audio_delta(evt)

    async def _handle_context(self, context: LLMContext):
        first_context = self._context is None
        if first_context:
            self._context = context
            await self._process_completed_function_calls(send_new_results=False)
            self._synced_context_message_count = len(context.messages)
            await self.create_response()
            return

        messages = context.messages
        start_index = min(self._synced_context_message_count, len(messages))
        new_messages = messages[start_index:]
        self._context = context
        self._synced_context_message_count = len(messages)

        await self._process_completed_function_calls(send_new_results=True)

        forced_instructions = [
            _content_to_text(message.get("content")).strip()
            for message in new_messages
            if message.get("role") == "system"
            and _content_to_text(message.get("content")).strip()
        ]
        if forced_instructions:
            await self.create_response_with_instructions(
                "\n\n".join(forced_instructions)
            )
            return

        has_user_message = any(
            message.get("role") == "user"
            and _content_to_text(message.get("content")).strip()
            for message in new_messages
        )
        if has_user_message:
            if self._pending_forced_response_instructions:
                await self._drain_pending_forced_responses()
                return
            await self.create_response()

    async def create_response_with_instructions(
        self,
        instructions: str,
        *,
        priority: bool = False,
        metadata: Optional[dict] = None,
        enqueued_at: Optional[float] = None,
    ):
        instructions = instructions.strip()
        if not instructions:
            return

        entry = {
            "instructions": instructions,
            "priority": bool(priority),
            "metadata": dict(metadata or {}),
            "enqueued_at": (
                float(enqueued_at) if enqueued_at is not None else time.perf_counter()
            ),
        }
        if entry["priority"]:
            entry["metadata"].setdefault("kind", "priority_forced_response")

        if not self._api_session_ready:
            self._enqueue_forced_response_entry(entry)
            self._run_llm_when_api_session_ready = True
            return

        active_response = (
            self._current_assistant_response
            or self._response_create_in_flight
            or self._response_setup_in_progress
        )
        monitor_state = getattr(self, "_realtime_monitor_state", None)
        bot_speaking = bool(monitor_state and monitor_state.get("bot_speaking"))
        if active_response or bot_speaking:
            self._enqueue_forced_response_entry(
                entry,
                active_response=bool(active_response),
                bot_speaking=bot_speaking,
            )
            return

        await self._create_forced_response_now(entry)

    async def create_response(self):
        if not self._api_session_ready:
            self._run_llm_when_api_session_ready = True
            return

        if self._pending_forced_response_instructions:
            entry = self._pending_forced_response_instructions[0]
            logging.info(
                "Skip Azure Realtime response.create; forced response pending. priority=%s reason=%s",
                bool(entry.get("priority")),
                (entry.get("metadata") or {}).get("reason", ""),
            )
            await self._drain_pending_forced_responses()
            return

        if (
            self._current_assistant_response
            or self._response_create_in_flight
            or self._response_setup_in_progress
        ):
            if self._barge_in_cancel_pending:
                self._create_response_after_cancel = True
                logging.info(
                    "Deferring Azure Realtime response.create until confirmed "
                    "barge-in cancellation completes."
                )
            logging.info(
                "Skip Azure Realtime response.create; response already active."
            )
            return

        self._barge_in_cancel_pending = False
        self._create_response_after_cancel = False
        self._response_setup_in_progress = True
        try:
            await self._create_response()
        finally:
            self._response_setup_in_progress = False

    async def append_context_message_and_create_response(
        self,
        context: LLMContext,
        message: dict,
        *,
        priority: bool = False,
        metadata: Optional[dict] = None,
    ):
        context.add_message(message)
        if self._context is context:
            self._synced_context_message_count = max(
                self._synced_context_message_count,
                len(context.messages),
            )
        reason = (metadata or {}).get("reason", "context_append")
        await self.create_response_with_instructions(
            _content_to_text(message.get("content")),
            priority=priority,
            metadata={**(metadata or {}), "reason": reason},
        )

    async def commit_input_audio_buffer(self) -> bool:
        if not self._api_session_ready:
            logging.info(
                "Skip Azure Realtime input_audio_buffer.commit; API session not ready."
            )
            return False
        input_audio_ms = self._uncommitted_input_audio_ms
        if input_audio_ms < 100.0:
            logging.info(
                "Skip Azure Realtime input_audio_buffer.commit; buffered input audio too small: %.2fms",
                input_audio_ms,
            )
            self._has_uncommitted_input_audio = False
            self._uncommitted_input_audio_ms = 0.0
            return False
        if self._manual_input_audio_commit_sent:
            logging.info(
                "Skip Azure Realtime input_audio_buffer.commit; manual commit already sent for this call."
            )
            return False
        if not self._has_uncommitted_input_audio:
            logging.info(
                "Skip Azure Realtime input_audio_buffer.commit; no buffered input audio available."
            )
            return False
        logging.info("Sending Azure Realtime input_audio_buffer.commit")
        await self.send_client_event(InputAudioBufferCommitEvent())
        self._has_uncommitted_input_audio = False
        self._uncommitted_input_audio_ms = 0.0
        self._manual_input_audio_commit_sent = True
        return True

    async def append_user_message_and_create_response(
        self,
        context: LLMContext,
        text: str,
        item_id: str = "",
    ):
        text = text.strip()
        if not text:
            return False
        item_id = (item_id or "").strip()
        handled_ids = getattr(self, "_handled_barge_in_item_ids", None)
        if handled_ids is None:
            handled_ids = set()
            self._handled_barge_in_item_ids = handled_ids
        if item_id and item_id in handled_ids:
            logging.debug(
                "Skip duplicate confirmed Realtime barge-in item: %s",
                item_id,
            )
            return False
        if item_id:
            handled_ids.add(item_id)
        context.add_message({"role": "user", "content": text})
        if self._context is context:
            self._synced_context_message_count = max(
                self._synced_context_message_count,
                len(context.messages),
            )
        await self.create_response()
        return True

    def _enqueue_forced_response_entry(
        self,
        entry: dict,
        *,
        active_response: bool = False,
        bot_speaking: bool = False,
    ):
        if entry.get("priority"):
            self._pending_forced_response_instructions.insert(0, entry)
        else:
            self._pending_forced_response_instructions.append(entry)
        logging.info(
            "Deferring forced Azure Realtime instructions until the current "
            "response/playback completes. priority=%s active_response=%s "
            "bot_speaking=%s reason=%s",
            bool(entry.get("priority")),
            active_response,
            bot_speaking,
            entry.get("metadata", {}).get("reason", ""),
        )

    async def _create_forced_response_now(self, entry):
        if isinstance(entry, str):
            entry = {
                "instructions": entry,
                "priority": False,
                "metadata": {},
                "enqueued_at": time.perf_counter(),
            }
        instructions = (entry.get("instructions") or "").strip()
        if not instructions:
            return
        dispatch_at = time.perf_counter()
        enqueued_at = float(entry.get("enqueued_at") or dispatch_at)
        queue_wait_ms = max(0.0, (dispatch_at - enqueued_at) * 1000.0)
        metadata = entry.get("metadata") or {}
        self._active_forced_response_entry = {
            **entry,
            "dispatch_at": dispatch_at,
            "queue_wait_ms": queue_wait_ms,
        }
        logging.info(
            "Creating forced Azure Realtime response: priority=%s reason=%s "
            "queue_wait_ms=%.1f instructions=%r",
            bool(entry.get("priority")),
            metadata.get("reason", ""),
            queue_wait_ms,
            _summarize_for_log(instructions),
        )
        forced_item = ConversationItem(
            type="message",
            role="system",
            content=[
                ItemContent(
                    type="input_text",
                    text=instructions,
                )
            ],
        )
        if hasattr(self, "_messages_added_manually"):
            self._messages_added_manually[forced_item.id] = True
        await self.send_client_event(ConversationItemCreateEvent(item=forced_item))
        await self.push_frame(LLMFullResponseStartFrame())
        await self.start_processing_metrics()
        await self.send_client_event(
            ResponseCreateEvent(
                response=ResponseProperties(
                    output_modalities=self._get_enabled_modalities(),
                    instructions=instructions,
                )
            )
        )
        await self.start_ttfb_metrics()
        metadata = self._active_forced_response_entry.get("metadata", {})
        logging.info(
            "Forced Azure Realtime response dispatched: priority=%s reason=%s queue_wait_ms=%.1f",
            bool(self._active_forced_response_entry.get("priority")),
            metadata.get("reason", ""),
            self._active_forced_response_entry.get("queue_wait_ms", 0.0),
        )

    async def cancel_current_response_for_barge_in(self, event: dict = None):
        active_response = (
            self._current_assistant_response
            or self._response_create_in_flight
            or self._response_setup_in_progress
        )
        monitor_state = getattr(self, "_realtime_monitor_state", None)
        bot_speaking = bool(monitor_state and monitor_state.get("bot_speaking"))
        if not active_response and not bot_speaking:
            logging.info(
                "Skip Azure Realtime barge-in cancel; no active response or playback."
            )
            return False

        if active_response:
            self._barge_in_cancel_pending = True
        logging.info(
            "Interrupting Azure Realtime playback for confirmed barge-in: "
            "active_response=%s bot_speaking=%s text=%r",
            bool(active_response),
            bot_speaking,
            _summarize_for_log((event or {}).get("text", "")),
        )
        await self.broadcast_interruption()
        if active_response:
            await self.send_client_event(ResponseCancelEvent())
        return True

    async def _drain_pending_forced_responses(self):
        if (
            not self._api_session_ready
            or self._current_assistant_response
            or self._response_create_in_flight
            or self._response_setup_in_progress
        ):
            return

        if self._pending_forced_response_instructions:
            entry = self._pending_forced_response_instructions.pop(0)
            await self.create_response_with_instructions(
                entry.get("instructions", ""),
                priority=bool(entry.get("priority")),
                metadata=entry.get("metadata") or {},
                enqueued_at=entry.get("enqueued_at"),
            )

    async def _handle_evt_session_updated(self, evt):
        self._api_session_ready = True
        if not self._audio_resumed_after_session_update:
            self.set_audio_input_paused(False)
            self._audio_resumed_after_session_update = True
            logging.info("Azure Realtime audio input resumed after session.update.")
        if self._run_llm_when_api_session_ready:
            self._run_llm_when_api_session_ready = False
            await self.create_response()
        await self._drain_pending_forced_responses()

    async def handle_evt_input_audio_transcription_completed(self, evt):
        metrics_collector = getattr(self, "_metrics_collector", None)
        usage = getattr(evt, "usage", None)
        if usage and metrics_collector and hasattr(
            metrics_collector,
            "add_realtime_transcription_usage",
        ):
            try:
                metrics_collector.add_realtime_transcription_usage(usage)
            except Exception as exc:
                logging.warning(
                    "Failed to record Azure Realtime transcription usage: %s",
                    exc,
                )
        await super().handle_evt_input_audio_transcription_completed(evt)

    async def _handle_evt_response_done(self, evt):
        if self._metrics_collector and hasattr(
            self._metrics_collector, "add_realtime_token_usage"
        ):
            self._metrics_collector.add_realtime_token_usage(
                getattr(evt.response, "usage", None)
            )
        active_forced_response_entry = self._active_forced_response_entry
        if self._metrics_collector and active_forced_response_entry:
            dispatch_at = active_forced_response_entry.get("dispatch_at")
            enqueued_at = active_forced_response_entry.get("enqueued_at")
            queue_wait_ms = active_forced_response_entry.get("queue_wait_ms", 0.0)
            first_token_at = time.perf_counter()
            model_ttfb_ms = None
            end_to_end_ttfb_ms = None
            if dispatch_at is not None:
                model_ttfb_ms = max(0.0, (first_token_at - dispatch_at) * 1000.0)
            if enqueued_at is not None:
                end_to_end_ttfb_ms = max(0.0, (first_token_at - enqueued_at) * 1000.0)
            metadata = active_forced_response_entry.get("metadata") or {}
            logging.info(
                "Forced Azure Realtime TTFB split: reason=%s queue_wait_ms=%.1f model_ttfb_ms=%s end_to_end_ttfb_ms=%s",
                metadata.get("reason", ""),
                queue_wait_ms,
                f"{model_ttfb_ms:.1f}" if model_ttfb_ms is not None else "",
                f"{end_to_end_ttfb_ms:.1f}" if end_to_end_ttfb_ms is not None else "",
            )
            if hasattr(self._metrics_collector, "record_forced_response_ttfb"):
                self._metrics_collector.record_forced_response_ttfb(
                    reason=metadata.get("reason", ""),
                    queue_wait_ms=queue_wait_ms,
                    model_ttfb_ms=model_ttfb_ms,
                    end_to_end_ttfb_ms=end_to_end_ttfb_ms,
                )
        self._active_forced_response_entry = None
        await super()._handle_evt_response_done(evt)
        self._response_create_in_flight = False
        self._set_realtime_monitor_flag("assistant_response_active", False)
        if self._create_response_after_cancel:
            self._create_response_after_cancel = False
            self._barge_in_cancel_pending = False
            await self.create_response()
            return
        await self._drain_pending_forced_responses()

    async def _maybe_handle_evt_retrieve_conversation_item_error(self, evt):
        if getattr(evt.error, "code", "") == "item_delete_invalid_item_id":
            logging.debug(
                "Ignoring idempotent Azure Realtime item delete error: %s",
                getattr(evt.error, "message", ""),
            )
            return True
        return await super()._maybe_handle_evt_retrieve_conversation_item_error(evt)

    async def delete_conversation_item_once(self, item_id: str):
        if not item_id:
            return False
        deleted_ids = getattr(self, "_deleted_conversation_item_ids", None)
        if deleted_ids is None:
            deleted_ids = set()
            self._deleted_conversation_item_ids = deleted_ids
        if item_id in deleted_ids:
            logging.debug(
                "Skip duplicate Azure Realtime conversation item delete: %s",
                item_id,
            )
            return False
        deleted_ids.add(item_id)
        await self.send_client_event(ConversationItemDeleteEvent(item_id=item_id))
        logging.info(
            "Deleted suppressed Realtime input item from server conversation: %s",
            item_id,
        )
        return True
