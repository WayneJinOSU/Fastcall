from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from fastcall_adapter.application.health import AdapterHealthService


router = APIRouter()


@router.get("/state/up")
@router.get("/up")
async def up():
    return {"status": "up"}


@router.get("/state/ready")
@router.get("/ready")
async def ready(request: Request):
    if not request.app.state.ready:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    content = await AdapterHealthService(
        request.app.state.database,
        request.app.state.freeswitch_gateway,
        request.app.state.settings,
    ).check()
    return JSONResponse(status_code=200 if content["status"] != "not_ready" else 503, content=content)
