import logging
from typing import Any

from pipecat.frames.frames import OutputTransportMessageUrgentFrame

from collection_agents.transport.copilot_websocket import (
    Role,
    create_hangup_event,
    create_heartbeat_event,
    create_text_event,
)
from collection_agents.transport.websocket_safe_send import safe_send_ws_message


class CopilotProtocolClient:
    def __init__(
        self,
        *,
        transport,
        websocket,
        call_uuid,
        task_uuid,
        business_id,
        on_disconnect,
    ):
        self._transport = transport
        self._websocket = websocket
        self._call_uuid = call_uuid
        self._task_uuid = task_uuid
        self._business_id = business_id
        self._on_disconnect = on_disconnect

    async def send_event(self, event_payload: dict[str, Any], *, description: str) -> bool:
        return await safe_send_ws_message(
            lambda: self._transport.output().send_message(
                OutputTransportMessageUrgentFrame(message=event_payload)
            ),
            websocket=self._websocket,
            description=description,
            logger=logging.getLogger(__name__),
            on_disconnect=self._on_disconnect,
        )

    async def send_text(self, text: str, role: str = Role.ROBOT, *, source: str = "unknown") -> bool:
        event_payload = create_text_event(
            call_uuid=self._call_uuid,
            task_uuid=self._task_uuid,
            business_id=self._business_id,
            text=text,
            role=role,
        )
        logging.info(
            "Sending copilot text event: source=%s role=%s text=%r call_uuid=%s",
            source,
            role,
            text,
            self._call_uuid,
        )
        sent = await self.send_event(
            event_payload,
            description=f"copilot text event source={source} role={role}",
        )
        logging.info(
            "Copilot text event send completed: source=%s role=%s sent=%s",
            source,
            role,
            sent,
        )
        return sent

    async def send_heartbeat(self) -> bool:
        return await self.send_event(
            create_heartbeat_event(
                call_uuid=self._call_uuid,
                task_uuid=self._task_uuid,
                business_id=self._business_id,
            ),
            description="copilot heartbeat event",
        )

    async def send_hangup(self) -> bool:
        return await self.send_event(
            create_hangup_event(
                call_uuid=self._call_uuid,
                task_uuid=self._task_uuid,
                business_id=self._business_id,
            ),
            description="copilot hangup event",
        )
