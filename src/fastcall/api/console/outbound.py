from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from fastcall.api.console.auth import ConsoleSession, console_session, console_write_session
from fastcall.api.dependencies import get_secret_codec, get_uow
from fastcall.api.requests import RequestModel
from fastcall.application.outbound_queries import OutboundQueryUseCase
from fastcall.application.outbound_tasks import OutboundTasksUseCase
from fastcall.application.task_control import CancelTaskUseCase


router = APIRouter(prefix="/console/api/v1")


class StateVersionRequest(RequestModel):
    expected_state_version: int = Field(ge=1)


class CancelRequest(StateVersionRequest):
    hangup_active: bool = True


def _tasks(uow, codec, request):
    return OutboundTasksUseCase(uow, codec, request.app.state.phone_hmac)


@router.get("/outbound-tasks")
def list_tasks(
    status: str | None = None,
    robot_app_id: str | None = None,
    search: str | None = Query(default=None, max_length=255),
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=200),
    uow=Depends(get_uow),
    session: ConsoleSession = Depends(console_session),
):
    return OutboundQueryUseCase(uow).list_tasks(
        status, robot_app_id, search, cursor, limit
    )


@router.get("/outbound-tasks/{task_id}")
def get_task(task_id: str, uow=Depends(get_uow), session=Depends(console_session)):
    return OutboundQueryUseCase(uow).get_console_task(task_id)


@router.get("/outbound-tasks/{task_id}/contacts")
def list_contacts(
    task_id: str,
    status: str | None = None,
    result_code: str | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    uow=Depends(get_uow),
    session=Depends(console_session),
):
    return OutboundQueryUseCase(uow).list_contacts(
        task_id, status, result_code, cursor, limit
    )


@router.get("/outbound-tasks/{task_id}/calls")
def list_calls(
    task_id: str,
    status: str | None = None,
    result_code: str | None = None,
    contact_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    uow=Depends(get_uow),
    session=Depends(console_session),
):
    return OutboundQueryUseCase(uow).list_console_calls(
        task_id, status, result_code, contact_id, cursor, limit
    )


@router.post("/outbound-tasks/{task_id}/start")
def start_task(
    task_id: str,
    data: StateVersionRequest,
    request: Request,
    uow=Depends(get_uow),
    codec=Depends(get_secret_codec),
    session=Depends(console_write_session),
):
    task = _tasks(uow, codec, request).start(task_id, data.expected_state_version)
    return OutboundQueryUseCase(uow).get_console_task(task["task_id"])


@router.post("/outbound-tasks/{task_id}/pause")
def pause_task(
    task_id: str,
    data: StateVersionRequest,
    request: Request,
    uow=Depends(get_uow),
    codec=Depends(get_secret_codec),
    session=Depends(console_write_session),
):
    task = _tasks(uow, codec, request).pause(task_id, data.expected_state_version)
    return OutboundQueryUseCase(uow).get_console_task(task["task_id"])


@router.post("/outbound-tasks/{task_id}/resume")
def resume_task(
    task_id: str,
    data: StateVersionRequest,
    request: Request,
    uow=Depends(get_uow),
    codec=Depends(get_secret_codec),
    session=Depends(console_write_session),
):
    task = _tasks(uow, codec, request).resume(task_id, data.expected_state_version)
    return OutboundQueryUseCase(uow).get_console_task(task["task_id"])


@router.post("/outbound-tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    data: CancelRequest,
    request: Request,
    uow=Depends(get_uow),
    session=Depends(console_write_session),
):
    task = await CancelTaskUseCase(uow, request.app.state.hangup_client).execute(
        task_id, data.expected_state_version, data.hangup_active
    )
    return OutboundQueryUseCase(uow).get_console_task(task["task_id"])


@router.get("/calls/{call_id}")
def get_call(call_id: str, uow=Depends(get_uow), session=Depends(console_session)):
    return OutboundQueryUseCase(uow).get_console_call(call_id)
