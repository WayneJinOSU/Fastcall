import asyncio
import json
from urllib.parse import urlsplit, urlunsplit


class ESLError(RuntimeError):
    pass


class ESLConnection:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.reader = None
        self.writer = None
        self.command_lock = asyncio.Lock()

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        response = await self.read_message()
        if response.get("Content-Type") != "auth/request":
            raise ESLError("FreeSWITCH did not request authentication")
        self.writer.write(f"auth {self.password}\r\n\r\n".encode("utf-8"))
        await self.writer.drain()
        response = await self.read_message()
        if not response.get("Reply-Text", "").startswith("+OK"):
            raise ESLError("FreeSWITCH ESL authentication failed")

    async def close(self):
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
        self.reader = self.writer = None

    async def read_message(self):
        headers = {}
        while True:
            line = await self.reader.readline()
            if not line:
                raise ESLError("FreeSWITCH ESL connection closed")
            if line in (b"\r\n", b"\n"):
                break
            key, separator, value = line.decode("utf-8", "replace").partition(":")
            if separator:
                headers[key.strip()] = value.strip()
        length = int(headers.get("Content-Length", "0"))
        if length:
            body = (await self.reader.readexactly(length)).decode("utf-8", "replace")
            headers["_body"] = body
            for line in body.splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    headers.setdefault(key.strip(), value.strip())
        return headers

    async def command(self, command):
        async with self.command_lock:
            if self.writer is None:
                await self.connect()
            self.writer.write(command.encode("utf-8") + b"\r\n\r\n")
            await self.writer.drain()
            response = await self.read_message()
            if "_body" in response and "Reply-Text" not in response:
                response["Reply-Text"] = response["_body"]
            return response


class FreeSwitchESL:
    def __init__(self, host, port, password, gateway, media_ws_url, dial_mode="gateway"):
        self.commands = ESLConnection(host, port, password)
        self.gateway = gateway
        self.media_ws_url = media_ws_url
        self.dial_mode = dial_mode

    async def health_check(self):
        response = await self.commands.command("api status")
        if "_body" not in response:
            raise ESLError("FreeSWITCH status response was empty")
        return True

    async def originate(self, call_id, origination_uuid, callee, task_id, contact_id):
        variables = ",".join((
            f"origination_uuid={origination_uuid}",
            f"fastcall_call_id={call_id}",
            f"fastcall_task_id={task_id}",
            f"fastcall_contact_id={contact_id}",
            "STREAM_PLAYBACK=true",
            "STREAM_BUFFER_SIZE=20",
        ))
        if self.dial_mode == "loopback":
            destination = f"loopback/{callee.lstrip('+')}"
        else:
            destination = f"sofia/gateway/{self.gateway}/{callee}"
        command = f"bgapi originate {{{variables}}}{destination} &park()"
        response = await self.commands.command(command)
        reply = response.get("Reply-Text", "")
        if not reply.startswith("+OK"):
            raise ESLError(reply or "originate was rejected")
        job_uuid = reply.split(":", 1)[1].strip() if ":" in reply else None
        return {"uuid": origination_uuid, "job_uuid": job_uuid}

    async def start_media(self, channel_uuid, call_id, claim_generation):
        metadata = json.dumps({
            "call_id": call_id,
            "claim_generation": claim_generation,
            "direction": "caller",
        }, separators=(",", ":"))
        parsed = urlsplit(self.media_ws_url)
        url = urlunsplit((
            parsed.scheme, parsed.netloc, f"{parsed.path.rstrip('/')}/{call_id}",
            parsed.query, "",
        ))
        response = await self.commands.command(
            f"api uuid_audio_stream {channel_uuid} start {url} mono 8k {metadata}"
        )
        if not response.get("Reply-Text", "").startswith("+OK"):
            raise ESLError("Unable to start FreeSWITCH audio stream")

    async def stop_media(self, channel_uuid):
        response = await self.commands.command(f"api uuid_audio_stream {channel_uuid} stop")
        reply = response.get("Reply-Text", "")
        if not reply.startswith("+OK") and "not found" not in reply.lower():
            raise ESLError("Unable to stop FreeSWITCH audio stream")

    async def listen_events(self, callback):
        events = ESLConnection(
            self.commands.host, self.commands.port, self.commands.password
        )
        await events.connect()
        await events.command(
            "event plain CHANNEL_CREATE CHANNEL_PROGRESS CHANNEL_ANSWER "
            "CHANNEL_DESTROY CHANNEL_HANGUP_COMPLETE"
        )
        while True:
            message = await events.read_message()
            if "Event-Name" in message:
                await callback(message)

    async def channel_exists(self, channel_uuid):
        if not channel_uuid:
            return False
        response = await self.commands.command(f"api uuid_exists {channel_uuid}")
        return response.get("Reply-Text", "").strip().lower() == "true"

    async def kill_channel(self, channel_uuid, cause="NORMAL_CLEARING"):
        response = await self.commands.command(f"api uuid_kill {channel_uuid} {cause}")
        reply = response.get("Reply-Text", "")
        if not reply.startswith("+OK") and "no such channel" not in reply.lower():
            raise ESLError("Unable to kill FreeSWITCH channel")
