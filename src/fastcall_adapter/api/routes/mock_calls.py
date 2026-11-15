import hmac

from fastapi import APIRouter, WebSocket


router = APIRouter()


@router.websocket("/internal/v1/mock-calls/{task_id}/browser")
async def mock_call_browser(websocket: WebSocket, task_id: str):
    expected = websocket.app.state.settings.adapter_token
    authorization = websocket.headers.get("authorization", "")
    provided = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    if not expected or not hmac.compare_digest(provided, expected):
        await websocket.close(code=1008)
        return
    runtime = websocket.app.state.mock_call_media
    if runtime is None:
        await websocket.close(code=1011)
        return
    await websocket.accept()
    try:
        await runtime.run_browser(task_id, websocket)
    except Exception:
        await websocket.close(code=1011)
        raise


@router.websocket("/internal/media/{call_id}/browser")
async def mock_call_freeswitch(websocket: WebSocket, call_id: str):
    expected = websocket.app.state.settings.media_token
    provided = websocket.query_params.get("token", "")
    if not expected or not hmac.compare_digest(provided, expected):
        await websocket.close(code=1008)
        return
    runtime = websocket.app.state.mock_call_media
    if runtime is None:
        await websocket.close(code=1011)
        return
    await websocket.accept()
    try:
        await runtime.run_freeswitch(call_id, websocket)
    except Exception:
        await websocket.close(code=1011)
        raise
