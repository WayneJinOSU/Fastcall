import httpx

from fastcall_core.domain.errors import RobotConfigError


class AgentReportClient:
    def __init__(self, base_url, agent_token):
        self.base_url = base_url.rstrip("/")
        self.agent_token = agent_token

    async def list_records(self, filters):
        return await self._get("/internal/v1/call-records", params=filters)

    async def get_summary(self, filters):
        return await self._get("/internal/v1/call-records/summary", params=filters)

    async def get_daily(self, filters):
        return await self._get("/internal/v1/call-records/daily", params=filters)

    async def get_facets(self):
        return await self._get("/internal/v1/call-records/facets")

    async def get_record(self, record_id):
        return await self._get(f"/internal/v1/call-records/{record_id}")

    async def _get(self, path, params=None):
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=10, follow_redirects=False
            ) as client:
                response = await client.get(
                    f"{self.base_url}{path}",
                    params={
                        key: value for key, value in (params or {}).items()
                        if value not in (None, "")
                    },
                    headers={"Authorization": f"Bearer {self.agent_token}"},
                )
        except httpx.TimeoutException as error:
            raise RobotConfigError(
                "AGENT_REPORT_TIMEOUT", "Agent report request timed out"
            ) from error
        except httpx.HTTPError as error:
            raise RobotConfigError(
                "AGENT_REPORT_UNAVAILABLE", "Agent report request failed"
            ) from error
        if response.status_code == 404:
            raise RobotConfigError("AGENT_CALL_NOT_FOUND", "Agent call record not found")
        if response.status_code != 200:
            raise RobotConfigError(
                "AGENT_REPORT_UNAVAILABLE", "Agent report request failed"
            )
        return response.json()
