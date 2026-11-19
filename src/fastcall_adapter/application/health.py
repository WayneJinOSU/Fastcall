import asyncio

import httpx


class AdapterHealthService:
    def __init__(self, database, gateway, settings):
        self.database = database
        self.gateway = gateway
        self.settings = settings

    async def check(self):
        common = await self._common_health()
        sip = await self._capability_health("sip", common, self.settings.sip_enabled)
        status = "ready" if common == "ready" and sip == "ready" else "not_ready"
        return {"status": status, "common": common, "sip": sip}

    async def _common_health(self):
        try:
            await asyncio.to_thread(self.database.ping)
            await self.gateway.health_check()
            await self._agent_health()
            return "ready"
        except Exception:
            return "not_ready"

    async def _capability_health(self, capability, common, enabled):
        if not enabled:
            return "disabled"
        if common != "ready":
            return "not_ready"
        try:
            await self.gateway.health_check(capability)
            return "ready"
        except Exception:
            return "not_ready"

    async def _agent_health(self):
        url = self.settings.collection_agent_http_url.rstrip("/") + "/state/up"
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            response = await client.get(url)
            response.raise_for_status()
