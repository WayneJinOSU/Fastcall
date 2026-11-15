import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from collection_agents.storage.reports import CallReportRepository


router = APIRouter(prefix="/internal/v1")


def _authorize(authorization: str = Header(default="")):
    expected = os.getenv("FASTCALL_AGENT_TOKEN", "")
    provided = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Agent report authentication failed")


@router.get("/call-records")
def list_call_records(
    start_at: str | None = None,
    end_at: str | None = None,
    robot_code: str | None = None,
    bot_mode: str | None = None,
    source: str = "all",
    call_uuid: str | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    _=Depends(_authorize),
):
    if source not in {"all", "fastcall_outbound", "console_voice_test", "generic_ws", "unclassified"}:
        raise HTTPException(status_code=400, detail="Invalid call source")
    try:
        return CallReportRepository().list_records(
            start_at=start_at, end_at=end_at, robot_code=robot_code,
            bot_mode=bot_mode, source=source, call_uuid=call_uuid,
            cursor=cursor, limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/call-records/summary")
def get_summary(
    start_at: str | None = None,
    end_at: str | None = None,
    robot_code: str | None = None,
    bot_mode: str | None = None,
    _=Depends(_authorize),
):
    try:
        return CallReportRepository().get_summary(
            start_at=start_at, end_at=end_at,
            robot_code=robot_code, bot_mode=bot_mode,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/call-records/daily")
def get_daily(
    start_at: str,
    end_at: str,
    robot_code: str | None = None,
    bot_mode: str | None = None,
    _=Depends(_authorize),
):
    try:
        return CallReportRepository().get_daily(
            start_at=start_at, end_at=end_at,
            robot_code=robot_code, bot_mode=bot_mode,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/call-records/facets")
def get_facets(_=Depends(_authorize)):
    return CallReportRepository().get_facets()


@router.get("/call-records/{record_id}")
def get_call_record(record_id: int, _=Depends(_authorize)):
    record = CallReportRepository().get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Agent call record not found")
    return record
