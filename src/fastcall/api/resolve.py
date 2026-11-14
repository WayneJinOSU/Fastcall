from fastapi import APIRouter, Depends

from fastcall.api.dependencies import get_uow, resolve_auth
from fastcall.api.requests import ResolveRequest
from fastcall.application.resolve import ResolveUseCase


router = APIRouter()


@router.post("/internal/v1/runtime/resolve", dependencies=[Depends(resolve_auth)])
def resolve(payload: ResolveRequest, uow=Depends(get_uow)):
    return ResolveUseCase(uow).execute(payload.robot_code)
