from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from robot_config_service.api.dependencies import admin_actor, get_uow
from robot_config_service.api.requests import RequestModel
from robot_config_service.application.call_checks import CallCheckUseCase
from robot_config_service.application.dto import utcnow
from robot_config_service.application.outbound_queries import OutboundQueryUseCase
from robot_config_service.domain.errors import RobotConfigError


router = APIRouter(prefix="/adminapi/v1", tags=["outbound-admin"])


class ReviewRequest(RequestModel):
    expected_state_version: int = Field(ge=1)
    resolution: str
    result_code: str | None = Field(default=None, max_length=64)
    evidence_type: str = Field(min_length=1, max_length=64)
    evidence_ref: str = Field(min_length=1, max_length=255)
    note: str = Field(min_length=1, max_length=2000)
    force_release: bool = False


@router.get("/calls")
def list_calls(
    status: str, cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
    uow=Depends(get_uow), _=Depends(admin_actor),
):
    if status not in ("checking", "unknown"):
        raise RobotConfigError("INVALID_CALL_FILTER", "Admin queue only supports checking or unknown")
    return OutboundQueryUseCase(uow).list_calls(None, status, None, None, cursor, limit)


@router.get("/calls/{call_id}")
def get_call(call_id: str, uow=Depends(get_uow), _=Depends(admin_actor)):
    return OutboundQueryUseCase(uow).get_call(call_id)


@router.post("/webhook-deliveries/{delivery_id}:reset")
def reset_webhook(delivery_id: str, uow=Depends(get_uow), _=Depends(admin_actor)):
    uow.webhook_worker.reset_failed(delivery_id, utcnow())
    uow.commit()
    return {"delivery_id": delivery_id, "status": "pending"}


@router.post("/calls/{call_id}/review")
async def review_call(
    call_id: str, data: ReviewRequest, request: Request, uow=Depends(get_uow), actor=Depends(admin_actor),
):
    return await CallCheckUseCase(uow, request.app.state.freeswitch_gateway).review(
        call_id, data.model_dump(), actor
    )
