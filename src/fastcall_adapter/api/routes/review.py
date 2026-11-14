from fastapi import APIRouter, Depends, Request

from fastcall_adapter.api.dependencies import admin_actor, get_uow
from fastcall_adapter.api.schemas import ReviewRequest
from fastcall_adapter.application.call_checks import CallCheckUseCase


router = APIRouter(prefix="/adminapi/v1", tags=["outbound-admin"])


@router.post("/calls/{call_id}/review")
async def review_call(
    call_id: str, data: ReviewRequest, request: Request,
    uow=Depends(get_uow), actor=Depends(admin_actor),
):
    return await CallCheckUseCase(uow, request.app.state.freeswitch_gateway).review(
        call_id, data.model_dump(), actor
    )
