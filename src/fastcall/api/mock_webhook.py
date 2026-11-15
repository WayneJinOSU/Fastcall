import hashlib
import hmac
import json
import time

from fastapi import APIRouter, Request, Response


router = APIRouter(prefix="/internal")


@router.post("/mock-webhook", status_code=204)
async def receive_mock_webhook(request: Request):
    settings = request.app.state.settings
    secret = request.app.state.mock_webhook_secrets.get(settings.mock_callback_secret_ref, "")
    event_id = request.headers.get("X-FastCall-Event-Id", "")
    timestamp = request.headers.get("X-FastCall-Timestamp", "")
    signature = request.headers.get("X-FastCall-Signature", "")
    if not secret or not event_id or not timestamp.isdigit():
        return Response(status_code=401)
    if abs(int(time.time()) - int(timestamp)) > 300:
        return Response(status_code=401)
    body = await request.body()
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + event_id.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, f"sha256={expected}"):
        return Response(status_code=401)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=400)
    if not isinstance(payload, dict):
        return Response(status_code=400)
    return Response(status_code=204)
