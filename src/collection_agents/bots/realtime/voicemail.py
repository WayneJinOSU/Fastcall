import logging

from collection_agents.bots.realtime.prompts import build_voice_assistant_prompt
from collection_agents.bots.realtime.utils import (
    _append_context_message_without_llm,
    _summarize_for_log,
)


class RealtimeVoicemailHandler:
    def __init__(
        self,
        *,
        session_context,
        context,
        hangup_state,
        llm_service,
        locale,
        instant_close,
        on_hangup,
        on_end,
    ):
        self._session = session_context
        self._context = context
        self._hangup_state = hangup_state
        self._llm_service = llm_service
        self._locale = locale
        self._instant_close = instant_close
        self._on_hangup = on_hangup
        self._on_end = on_end

    def _mark_detected(self):
        self._session["voicemail_detected"] = True
        self._session["no_response_count"] = 0
        self._hangup_state.input_blocked = True
        if self._llm_service and hasattr(self._llm_service, "set_audio_input_paused"):
            self._llm_service.set_audio_input_paused(True)

    def _record_transcript(self, event):
        transcript = (
            event.get("transcript_text") or event.get("text") or ""
        ).strip()
        if not transcript:
            return
        item_id = str(event.get("item_id") or "").strip()
        dedupe_key = item_id or transcript
        seen_keys = self._session.setdefault("voice_assistant_context_record_keys", set())
        if dedupe_key in seen_keys:
            return
        seen_keys.add(dedupe_key)
        _append_context_message_without_llm(
            self._context,
            {"role": "user", "content": transcript},
        )
        self._session.setdefault("voice_assistant_transcripts", []).append({
            "item_id": item_id,
            "text": transcript,
            "classification_detail": event.get("classification_detail", ""),
        })
        logging.info(
            "Recorded realtime voice assistant transcript: item_id=%s text=%r",
            item_id,
            _summarize_for_log(transcript),
        )

    async def handle(self, event):
        event = event or {}
        if self._session.get("voicemail_detected"):
            return
        self._record_transcript(event)
        detail = event.get("classification_detail") or "assistant"
        is_voicemail = detail == "voicemail"
        self._mark_detected()
        logging.info(
            "Realtime voice assistant detected: item_id=%s detail=%s text=%r",
            event.get("item_id"),
            detail,
            (event.get("text") or "")[:180],
        )
        if is_voicemail and self._instant_close:
            await self.instant_close()
            return

        prompt = {
            "role": "system",
            "content": build_voice_assistant_prompt(self._locale),
        }
        if self._llm_service:
            await self._llm_service.cancel_current_response_for_barge_in(event)
            await self._llm_service.append_context_message_and_create_response(
                self._context,
                prompt,
                priority=True,
                metadata={
                    "reason": "voicemail" if is_voicemail else "voice_assistant",
                    "item_id": event.get("item_id", ""),
                    "classification_detail": detail,
                },
            )
        else:
            self._context.add_message(prompt)

    async def instant_close(self):
        if self._hangup_state.is_hanging_up:
            return
        self._hangup_state.is_hanging_up = True
        await self._on_hangup()
        await self._on_end("voicemail_instant_close")
