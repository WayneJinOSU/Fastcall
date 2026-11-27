import gzip
import uuid

import audioop

import websockets
from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
)
from pipecat.services.stt_service import WebsocketSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

from collection_agents.services.volcengine_protocol import (
    AUDIO_ONLY_REQUEST,
    COMPRESSION_GZIP,
    FULL_CLIENT_REQUEST,
    SERIALIZATION_JSON,
    SERIALIZATION_RAW,
    json_payload,
    make_packet,
    parse_packet,
)


class VolcengineSTTService(WebsocketSTTService):
    def __init__(
        self,
        *,
        api_key,
        resource_id="volc.seedasr.sauc.duration",
        model="bigmodel",
        locale="zh-CN",
        url="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
        connect_fn=None,
        **kwargs,
    ):
        super().__init__(sample_rate=8000, keepalive_timeout=20, **kwargs)
        self._api_key = api_key
        self._resource_id = resource_id
        self._model = model
        self._locale = locale
        self._url = url
        self._connect_fn = connect_fn or websockets.connect
        self._receive_task = None

    def can_generate_metrics(self):
        return True

    async def _connect(self):
        await super()._connect()
        await self._connect_websocket()

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._disconnect()

    async def _connect_websocket(self):
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        self._websocket = await self._connect_fn(self._url, additional_headers=headers)
        await self._call_event_handler("on_connected")
        payload = {
            "user": {"uid": "fastcall"},
            "audio": {
                "format": "pcm",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
                "language": self._locale,
            },
            "request": {
                "model_name": self._model,
                "enable_itn": True,
                "enable_punc": True,
                "show_utterances": True,
                "result_type": "single",
                "end_window_size": 600,
            },
        }
        packet = make_packet(
            FULL_CLIENT_REQUEST,
            0,
            SERIALIZATION_JSON,
            COMPRESSION_GZIP,
            gzip.compress(json_payload(payload)),
        )
        await self._websocket.send(packet)
        if self._receive_task is None:
            self._receive_task = self.create_task(
                self._receive_task_handler(self._report_error), name="volcengine-stt-receive"
            )

    async def _disconnect_websocket(self):
        if self._receive_task is not None:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        if self._websocket is not None:
            await self._websocket.close()
            self._websocket = None
        await self._call_event_handler("on_disconnected")

    async def _send_audio(self, audio):
        resampled, _ = audioop.ratecv(audio, 2, 1, 8000, 16000, None)
        packet = make_packet(
            AUDIO_ONLY_REQUEST,
            0,
            SERIALIZATION_RAW,
            COMPRESSION_GZIP,
            gzip.compress(resampled),
        )
        await self._websocket.send(packet)

    async def run_stt(self, audio):
        await self._send_audio(audio)
        yield None

    async def _send_keepalive(self, silence):
        await self._send_audio(silence)

    async def _receive_messages(self):
        async for message in self._websocket:
            response = parse_packet(message)
            payload = response["payload"]
            if response["message_type"] == 0b1111:
                logger.error(f"Volcengine ASR error: {payload}")
                await self.push_error(error_msg=f"Volcengine ASR error: {payload}")
                continue
            if not isinstance(payload, dict) or payload.get("code", 20000000) != 20000000:
                logger.error(f"Volcengine ASR response error: {payload}")
                continue
            result = payload.get("result")
            if isinstance(result, dict):
                results = [result]
            elif isinstance(result, list):
                results = result
            else:
                results = []
            for item in results:
                text = item.get("text", "").strip()
                if not text:
                    continue
                utterances = item.get("utterances") or []
                definite = any(utterance.get("definite") for utterance in utterances)
                timestamp = time_now_iso8601()
                language = Language(self._locale)
                if definite:
                    await self.push_frame(
                        TranscriptionFrame(text, self._user_id, timestamp, language, result=item)
                    )
                    await self.stop_processing_metrics()
                else:
                    await self.start_processing_metrics()
                    await self.push_frame(
                        InterimTranscriptionFrame(
                            text, self._user_id, timestamp, language, result=item
                        )
                    )
