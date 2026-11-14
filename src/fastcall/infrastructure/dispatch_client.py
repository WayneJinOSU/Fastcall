import httpx

from fastcall_core.domain.errors import RobotConfigError


class AdapterDispatchClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def dispatch(self, call_id):
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            response = await client.post(
                f"{self.base_url}/internal/v1/calls/{call_id}/dispatch",
                headers={"Authorization": f"Bearer {self.token}"},
            )
        if response.status_code != 200:
            raise RobotConfigError("ADAPTER_DISPATCH_FAILED", "Adapter dispatch request failed")
        return response.json()
