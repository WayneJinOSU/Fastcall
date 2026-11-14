import datetime

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import Field

from fastcall.api.dependencies import (
    get_secret_codec,
    get_uow,
    openapi_auth,
)
from fastcall.api.requests import RequestModel
from fastcall.application.outbound_queries import OutboundQueryUseCase
from fastcall.application.outbound_tasks import OutboundTasksUseCase
from fastcall.application.task_control import CancelTaskUseCase


router = APIRouter(prefix="/openapi/v1", tags=["outbound-openapi"])
auth = Depends(openapi_auth)


class TaskPutRequest(RequestModel):
    name: str = Field(min_length=1, max_length=255)
    robot_code: str = Field(min_length=1, max_length=128)
    timezone: str = "UTC"
    default_country_code: str = Field(pattern=r"^[0-9]{1,4}$")
    scheduled_start_at: datetime.datetime | None = None
    scheduled_end_at: datetime.datetime | None = None
    calling_windows: list = Field(default_factory=list)
    excluded_dates: list = Field(default_factory=list)
    max_concurrent_calls: int = Field(ge=1, le=10000)
    calls_per_minute: int = Field(ge=1, le=10000)
    retry_policy: dict
    callback_url: str | None = Field(default=None, max_length=2048)
    callback_secret_ref: str | None = Field(default=None, max_length=255)


class ContactItem(RequestModel):
    customer_contact_id: str = Field(min_length=1, max_length=128)
    phone: str = Field(min_length=1, max_length=32)
    variables: dict = Field(default_factory=dict)


class ImportContactsRequest(RequestModel):
    contacts: list[ContactItem] = Field(min_length=1, max_length=500)


class StateVersionRequest(RequestModel):
    expected_state_version: int = Field(ge=1)


class CancelRequest(StateVersionRequest):
    hangup_active: bool = True


def _tasks(uow, codec, request: Request):
    return OutboundTasksUseCase(uow, codec, request.app.state.phone_hmac)


@router.put("/outbound-tasks/{customer_task_id}")
def create_task(
    customer_task_id: str = Path(min_length=1, max_length=128),
    data: TaskPutRequest = None, request: Request = None,
    uow=Depends(get_uow), codec=Depends(get_secret_codec), _=auth,
):
    return _tasks(uow, codec, request).create(customer_task_id, data.model_dump())


@router.post("/outbound-tasks/{task_id}/contacts")
def import_contacts(task_id: str, data: ImportContactsRequest, request: Request, uow=Depends(get_uow), codec=Depends(get_secret_codec), _=auth):
    return _tasks(uow, codec, request).import_contacts(task_id, [item.model_dump() for item in data.contacts])


@router.get("/outbound-tasks/{task_id}")
def get_task(task_id: str, uow=Depends(get_uow), _=auth):
    return OutboundTasksUseCase(uow, None, None).get(task_id)


@router.post("/outbound-tasks/{task_id}/start")
def start_task(task_id: str, data: StateVersionRequest, request: Request, uow=Depends(get_uow), codec=Depends(get_secret_codec), _=auth):
    return _tasks(uow, codec, request).start(task_id, data.expected_state_version)


@router.post("/outbound-tasks/{task_id}/pause")
def pause_task(task_id: str, data: StateVersionRequest, request: Request, uow=Depends(get_uow), codec=Depends(get_secret_codec), _=auth):
    return _tasks(uow, codec, request).pause(task_id, data.expected_state_version)


@router.post("/outbound-tasks/{task_id}/resume")
def resume_task(task_id: str, data: StateVersionRequest, request: Request, uow=Depends(get_uow), codec=Depends(get_secret_codec), _=auth):
    return _tasks(uow, codec, request).resume(task_id, data.expected_state_version)


@router.post("/outbound-tasks/{task_id}/cancel")
async def cancel_task(task_id: str, data: CancelRequest, request: Request, uow=Depends(get_uow), _=auth):
    return await CancelTaskUseCase(uow, request.app.state.hangup_client).execute(
        task_id, data.expected_state_version, data.hangup_active
    )


@router.get("/outbound-tasks/{task_id}/contacts")
def list_contacts(
    task_id: str, status: str | None = None, result_code: str | None = None,
    cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
    uow=Depends(get_uow), _=auth,
):
    return OutboundQueryUseCase(uow).list_contacts(task_id, status, result_code, cursor, limit)


@router.get("/outbound-tasks/{task_id}/calls")
def list_calls(
    task_id: str, status: str | None = None, result_code: str | None = None,
    contact_id: str | None = None, cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200), uow=Depends(get_uow), _=auth,
):
    return OutboundQueryUseCase(uow).list_calls(task_id, status, result_code, contact_id, cursor, limit)


@router.get("/task-contacts/{contact_id}")
def get_contact(contact_id: str, uow=Depends(get_uow), _=auth):
    return OutboundQueryUseCase(uow).get_contact(contact_id)


@router.get("/calls/{call_id}")
def get_call(call_id: str, uow=Depends(get_uow), _=auth):
    return OutboundQueryUseCase(uow).get_call(call_id)
