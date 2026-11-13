import hmac

from fastapi import APIRouter, WebSocket


router = APIRouter()


@router.websocket("/internal/media/{call_id}")
async def media_stream(websocket: WebSocket, call_id: str):
    expected = websocket.app.state.settings.media_token
    provided = websocket.query_params.get("token", "")
    if not expected or not hmac.compare_digest(provided, expected):
        await websocket.close(code=1008)
        return
    runtime = websocket.app.state.media_runtime
    if runtime is None:
        await websocket.close(code=1011)
        return
    await websocket.accept()
    try:
        await runtime.attach_freeswitch(call_id, websocket)
    except Exception:
        await websocket.close(code=1011)
        raise
