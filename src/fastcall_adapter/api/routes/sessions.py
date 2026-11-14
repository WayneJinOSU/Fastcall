import datetime

from fastapi import APIRouter, Depends

from fastcall_adapter.api.dependencies import (
    adapter_auth,
    agent_auth,
    get_secret_codec,
    get_uow,
)
from fastcall_adapter.api.schemas import AgentSessionEventRequest, CallAnalysisRequest
from fastcall_core.application.call_analysis import SaveCallAnalysisUseCase
from fastcall_core.application.call_events import AgentSessionUseCase


router = APIRouter(prefix="/internal/v1", tags=["outbound-session"])


def _use_case(uow, codec):
    return AgentSessionUseCase(uow, codec)


@router.post("/calls/{call_id}/session-claim")
def claim_session(call_id: str, uow=Depends(get_uow), codec=Depends(get_secret_codec), _=Depends(adapter_auth)):
    return _use_case(uow, codec).claim(call_id)


@router.post("/calls/{call_id}/session-claims:recover")
def recover_session(call_id: str, uow=Depends(get_uow), codec=Depends(get_secret_codec), _=Depends(adapter_auth)):
    return _use_case(uow, codec).claim(call_id, recover=True)


@router.post("/calls/{call_id}/session-events/{action}")
def session_event(
    call_id: str,
    action: str,
    request: AgentSessionEventRequest,
    uow=Depends(get_uow),
    _=Depends(agent_auth),
):
    occurred_at = request.occurred_at.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return _use_case(uow, None).handle(
        call_id,
        action,
        request.claim_generation,
        request.agent_version,
        occurred_at,
        request.event_id,
        request.detail,
    )


@router.post("/calls/{call_id}/analysis")
def save_analysis(
    call_id: str, request: CallAnalysisRequest,
    uow=Depends(get_uow), _=Depends(agent_auth),
):
    return SaveCallAnalysisUseCase(uow).execute(
        call_id, request.business_result, request.analysis
    )
