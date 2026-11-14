from fastapi import APIRouter, Depends, Request

from fastcall_adapter.application.hangup import HangupCallUseCase
from fastcall_adapter.api.dependencies import adapter_auth, get_uow


router = APIRouter(prefix="/internal/v1", tags=["outbound-hangup"])


@router.post("/calls/{call_id}/hangup")
async def hangup(call_id: str, request: Request, uow=Depends(get_uow), _=Depends(adapter_auth)):
    return await HangupCallUseCase(
        uow, request.app.state.freeswitch_gateway
    ).execute(call_id)
