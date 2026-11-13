import asyncio
import datetime
import json
import os

from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport


class SessionFastAPIWebsocketTransport(FastAPIWebsocketTransport):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client.disconnect = self._disconnect_for_pipeline

    async def _disconnect_for_pipeline(self):
        self._client._leave_counter -= 1
        if self._client._leave_counter > 0:
            return
        self._client._closing = True


class SessionLifecycle:
    def __init__(self, websocket, call_id, generation, config_hash):
        self.websocket = websocket
        self.call_id = call_id
        self.generation = generation
        self.config_hash = config_hash
        self.agent_version = os.getenv("COLLECTION_AGENT_VERSION", "unknown")
        self.state = "claimed"

    async def _send(self, action, detail=None):
        event_id = f"agent:{self.call_id}:{self.generation}:{action}"
        event = {
            "event": f"session.{action}",
            "event_id": event_id,
            "call_id": self.call_id,
            "claim_generation": self.generation,
            "config_hash": self.config_hash,
            "agent_version": self.agent_version,
            "occurred_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "detail": detail or {},
        }
        while True:
            await self.websocket.send_json(event)
            try:
                message = await asyncio.wait_for(self.websocket.receive(), timeout=2)
                if message["type"] == "websocket.disconnect":
                    raise RuntimeError("Agent websocket disconnected while awaiting ACK")
                if message.get("text") is None:
                    continue
                ack = json.loads(message["text"])
                if ack.get("event") == "ack" and ack.get("event_id") == event_id:
                    self.state = action
                    return
            except asyncio.TimeoutError:
                continue

    async def attached(self):
        await self._send("attached")

    async def ready(self):
        await self._send("ready")

    async def completed(self, detail=None):
        await self._send("completed", detail)

    async def failed(self, message):
        if self.state != "completed":
            await self._send("failed", {"message": message})
