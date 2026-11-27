import asyncio
import uuid

import websockets
from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    TTSStartedFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import AudioContextTTSService
from websockets.protocol import State

from collection_agents.services.volcengine_protocol import make_event_packet, parse_packet


class VolcengineTTSService(AudioContextTTSService):
    def __init__(
        self,
        *,
        api_key,
        speaker,
        resource_id="seed-tts-2.0",
        model=None,
        speed=None,
        volume=None,
        url="wss://openspeech.bytedance.com/api/v3/tts/bidirection",
        connect_fn=None,
        **kwargs,
    ):
        super().__init__(sample_rate=8000, supports_word_timestamps=True, **kwargs)
        self._api_key = api_key
        self._speaker = speaker
        self._resource_id = resource_id
        self._model = model
        self._speed = speed
        self._volume = volume
        self._url = url
        self._connect_fn = connect_fn or websockets.connect
        self._receive_task = None
        self._events = {}
        self._session_id = None

    async def start(self, frame):
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._disconnect()

    async def _connect(self):
        await super()._connect()
        await self._connect_websocket()

    async def _wait_for_event(self, event, timeout=10):
        waiter = self._events[event]
        await asyncio.wait_for(waiter.wait(), timeout=timeout)

    def _event_payload(self, event, req_params=None):
        payload = {
            "user": {"uid": "fastcall"},
            "event": event,
            "namespace": "BidirectionalTTS",
        }
        if req_params is not None:
            payload["req_params"] = req_params
        return payload

    async def _connect_websocket(self):
        self._events = {
            50: asyncio.Event(),
            150: asyncio.Event(),
        }
        self._session_id = None
        self._websocket = await self._connect_fn(
            self._url,
            additional_headers={
                "X-Api-Key": self._api_key,
                "X-Api-Resource-Id": self._resource_id,
                "X-Api-Connect-Id": str(uuid.uuid4()),
            },
        )
        await self._websocket.send(make_event_packet(1, self._event_payload(1)))
        if self._receive_task is None:
            self._receive_task = self.create_task(
                self._receive_task_handler(self._report_error), name="volcengine-tts-receive"
            )
        await self._wait_for_event(50)
        await self._call_event_handler("on_connected")

    async def _disconnect_websocket(self):
        if self._websocket is not None and self._websocket.state is State.OPEN:
            await self._websocket.send(make_event_packet(2, self._event_payload(2)))
            await self._websocket.close()
        if self._receive_task is not None:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        self._websocket = None
        self._session_id = None
        await self._call_event_handler("on_disconnected")

    async def run_tts(self, text, context_id):
        if not self._websocket or self._websocket.state is State.CLOSED:
            await self._connect()
        if not self.has_active_audio_context():
            await self.start_ttfb_metrics()
            yield TTSStartedFrame(context_id=context_id)
            await self.create_audio_context(context_id)

        if self._session_id != context_id:
            self._events[150].clear()
            audio_params = {"format": "pcm", "sample_rate": self.sample_rate}
            if self._speed is not None:
                audio_params["speech_rate"] = round((float(self._speed) - 1) * 100)
            if self._volume is not None:
                audio_params["loudness_rate"] = round((float(self._volume) - 1) * 100)
            req_params = {
                "text": "",
                "speaker": self._speaker,
                "audio_params": audio_params,
            }
            if self._model:
                req_params["model"] = self._model
            start_payload = self._event_payload(100, req_params)
            await self._websocket.send(make_event_packet(100, start_payload, context_id))
            await self._wait_for_event(150)
            self._session_id = context_id

        task_payload = self._event_payload(200, {"text": text})
        await self._websocket.send(make_event_packet(200, task_payload, context_id))
        await self.start_tts_usage_metrics(text)
        yield None

    async def flush_audio(self):
        context_id = self.get_active_audio_context_id()
        if context_id and self._websocket and self._websocket.state is State.OPEN:
            payload = self._event_payload(102)
            await self._websocket.send(make_event_packet(102, payload, context_id))
            self.reset_active_audio_context()

    async def on_audio_context_interrupted(self, context_id):
        await self.stop_all_metrics()
        if context_id and self._websocket and self._websocket.state is State.OPEN:
            payload = self._event_payload(101)
            await self._websocket.send(make_event_packet(101, payload, context_id))
        if context_id and context_id == self._session_id:
            self._session_id = None

    async def _receive_messages(self):
        async for message in self._websocket:
            response = parse_packet(message)
            event = response["event"]
            session_id = response["session_id"]
            payload = response["payload"]
            if event in self._events:
                self._events[event].set()
            if event in (151, 152, 153) and session_id == self._session_id:
                self._session_id = None
            if event == 352:
                await self.stop_ttfb_metrics()
                await self.start_word_timestamps()
                frame = TTSAudioRawFrame(
                    audio=payload,
                    sample_rate=self.sample_rate,
                    num_channels=1,
                    context_id=session_id,
                )
                await self.append_to_audio_context(session_id, frame)
            elif event == 152:
                await self.stop_ttfb_metrics()
                await self.add_word_timestamps(
                    [("TTSStoppedFrame", 0), ("Reset", 0)], session_id
                )
                await self.remove_audio_context(session_id)
            elif event == 151:
                await self.add_word_timestamps([("TTSStoppedFrame", 0)], session_id)
                await self.remove_audio_context(session_id)
            elif event in {51, 153}:
                logger.error(f"Volcengine TTS session error: {payload}")
                if session_id:
                    await self.push_frame(TTSStoppedFrame(context_id=session_id))
                    await self.remove_audio_context(session_id)
                await self.push_frame(ErrorFrame(f"Volcengine TTS error: {payload}"))
            elif response["message_type"] == 0b1111:
                logger.error(f"Volcengine TTS protocol error: {payload}")
                await self.push_error(error_msg=f"Volcengine TTS error: {payload}")
