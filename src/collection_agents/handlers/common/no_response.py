import asyncio
import logging

from collection_agents.services.call_config import coerce_positive_int


class NoResponseController:
    def __init__(
        self,
        *,
        session_context,
        config_manager,
        is_hanging_up,
        on_reply,
        on_farewell,
        on_hangup,
        fallback_delay=20.0,
    ):
        self._session = session_context
        self._config_manager = config_manager
        self._is_hanging_up = is_hanging_up
        self._on_reply = on_reply
        self._on_farewell = on_farewell
        self._on_hangup = on_hangup
        self._fallback_delay = fallback_delay

    def register(self, silence_detector):
        silence_detector.event_handler("on_silence_detected")(self.handle)

    async def handle(self, logger=None, silence_count=None):
        session = self._session
        if session.get("is_end") or not session.get("on_answer"):
            return
        if session.get("voicemail_detected") or self._is_hanging_up():
            return
        if session.get("no_response_hangup_queued"):
            return

        config = self._config_manager.get_no_response_config()
        max_count = coerce_positive_int(
            config.get("maxNoResponseCount", 3),
            default_value=3,
            field_name="maxNoResponseCount",
        )
        session["no_response_count"] += 1
        current_round = session["no_response_count"]
        logging.info(
            "No-response detected: round=%s max=%s configured=%r",
            current_round,
            max_count,
            config.get("maxNoResponseCount"),
        )

        if current_round < max_count:
            message = config.get("noResponseReply") or "Hi, are you still here"
            await self._on_reply(message, current_round, max_count)
            return

        hangup_config = config.get("noResponseHangUp") or {}
        if not hangup_config.get("enabled", True):
            logging.info(
                "No-response max reached, hangup disabled: round=%s max=%s",
                current_round,
                max_count,
            )
            return

        session["no_response_hangup_queued"] = True
        await self._on_farewell(
            hangup_config.get("hangupMessage") or "",
            current_round,
            max_count,
        )
        if not session.get("no_response_hangup_task"):
            session["no_response_hangup_task"] = asyncio.create_task(
                self._fallback_hangup()
            )

    async def _fallback_hangup(self):
        try:
            await asyncio.sleep(self._fallback_delay)
            if self._session.get("is_end"):
                return
            await self._on_hangup()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.error("No-response fallback hangup task error: %s", exc)
