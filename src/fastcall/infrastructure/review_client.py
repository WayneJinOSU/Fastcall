import httpx

from fastcall_core.domain.errors import RobotConfigError


class AdapterReviewClient:
    def __init__(self, base_url, admin_token):
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token

    async def review(self, call_id, payload, actor):
        try:
            async with httpx.AsyncClient(
                trust_env=False, timeout=15, follow_redirects=False
            ) as client:
                return await client.post(
                    f"{self.base_url}/adminapi/v1/calls/{call_id}/review",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.admin_token}",
                        "X-Actor-Id": actor,
                    },
                )
        except httpx.TimeoutException as error:
            raise RobotConfigError(
                "ADMIN_REVIEW_TIMEOUT", "Adapter review request timed out"
            ) from error
        except httpx.HTTPError as error:
            raise RobotConfigError(
                "ADMIN_REVIEW_UNAVAILABLE", "Adapter review request failed"
            ) from error
