import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
import time
from urllib.parse import urlsplit

from httpcore import AsyncConnectionPool
from httpcore._backends.auto import AutoBackend

from robot_config_service.domain.errors import RobotConfigError


_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
}


class CallbackPolicy:
    def __init__(self, allowed_hostnames=(), allowed_cidrs=(), resolver=None):
        self.allowed_hostnames = {value.lower().rstrip(".") for value in allowed_hostnames}
        self.allowed_networks = [ipaddress.ip_network(value) for value in allowed_cidrs]
        self.resolver = resolver or self._resolve

    async def _resolve(self, hostname, port):
        loop = asyncio.get_running_loop()
        rows = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        return [row[4][0] for row in rows]

    def _allowed(self, hostname, address):
        return hostname in self.allowed_hostnames or any(
            address in network for network in self.allowed_networks
        )

    async def resolve(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RobotConfigError("INVALID_CALLBACK_URL", "Webhook endpoint must be HTTPS")
        hostname = parsed.hostname.lower().rstrip(".")
        port = parsed.port or 443
        addresses = []
        for value in await self.resolver(hostname, port):
            address = ipaddress.ip_address(value)
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if address in _METADATA_ADDRESSES or (
                address.is_unspecified or address.is_loopback or address.is_link_local
                or address.is_multicast
            ):
                raise RobotConfigError("CALLBACK_ADDRESS_FORBIDDEN", "Webhook IP address is forbidden")
            if not address.is_global and not self._allowed(hostname, address):
                raise RobotConfigError("CALLBACK_ADDRESS_FORBIDDEN", "Private webhook IP is not allowlisted")
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise RobotConfigError("CALLBACK_DNS_FAILED", "Webhook DNS returned no addresses")
        return hostname, addresses[0]


class PinnedNetworkBackend(AutoBackend):
    def __init__(self, address):
        super().__init__()
        self.address = str(address)

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return await super().connect_tcp(
            self.address, port, timeout=timeout, local_address=local_address,
            socket_options=socket_options,
        )


class SecureWebhookSender:
    def __init__(self, policy, clock=time.time):
        self.policy = policy
        self.clock = clock

    async def send(self, url, payload, event_id, secret):
        hostname, address = await self.policy.resolve(url)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(self.clock()))
        signature = hmac.new(
            secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + event_id.encode("utf-8") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-FastCall-Event-Id": event_id,
            "X-FastCall-Timestamp": timestamp,
            "X-FastCall-Signature": f"sha256={signature}",
        }
        pool = AsyncConnectionPool(network_backend=PinnedNetworkBackend(address))
        try:
            response = await pool.request(
                "POST", url, content=body, headers=headers,
                extensions={"timeout": {"connect": 5, "read": 10, "write": 10, "pool": 5}},
            )
            return response.status
        finally:
            await pool.aclose()
