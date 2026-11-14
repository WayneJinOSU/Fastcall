from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


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
    try:
        request.app.state.database.ping()
        return {"status": "ready"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
