from fastapi import APIRouter, Depends, Request

from robot_config_service.api.dependencies import adapter_auth
from robot_config_service.domain.errors import RobotConfigError


router = APIRouter(prefix="/internal/v1", tags=["outbound-dispatch"])


@router.post("/calls/{call_id}/dispatch")
async def dispatch(call_id: str, request: Request, _=Depends(adapter_auth)):
    if request.app.state.dispatcher is None:
        raise RobotConfigError("ADAPTER_UNAVAILABLE", "Adapter dispatcher is not configured")
    return await request.app.state.dispatcher.dispatch(call_id)
