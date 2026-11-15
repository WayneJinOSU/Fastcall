from fastapi import APIRouter, Depends, Query, Request

from fastcall.api.console.auth import console_session


router = APIRouter(prefix="/console/api/v1")


def _filters(start_at, end_at, robot_code, bot_mode, call_uuid=None, cursor=None, limit=None):
    return {
        "start_at": start_at,
        "end_at": end_at,
        "robot_code": robot_code,
        "bot_mode": bot_mode,
        "call_uuid": call_uuid,
        "cursor": cursor,
        "limit": limit,
    }


@router.get("/agent-call-records")
async def list_agent_call_records(
    request: Request,
    start_at: str | None = None,
    end_at: str | None = None,
    robot_code: str | None = None,
    bot_mode: str | None = None,
    call_uuid: str | None = None,
    cursor: str | None = None,
    limit: int = Query(200, ge=1, le=200),
    session=Depends(console_session),
):
    return await request.app.state.agent_report_client.list_records(_filters(
        start_at, end_at, robot_code, bot_mode, call_uuid, cursor, limit
    ))


@router.get("/agent-call-records/summary")
async def get_agent_summary(
    request: Request,
    start_at: str | None = None,
    end_at: str | None = None,
    robot_code: str | None = None,
    bot_mode: str | None = None,
    session=Depends(console_session),
):
    return await request.app.state.agent_report_client.get_summary(_filters(
        start_at, end_at, robot_code, bot_mode
    ))


@router.get("/agent-call-records/daily")
async def get_agent_daily(
    request: Request,
    start_at: str,
    end_at: str,
    robot_code: str | None = None,
    bot_mode: str | None = None,
    session=Depends(console_session),
):
    return await request.app.state.agent_report_client.get_daily(_filters(
        start_at, end_at, robot_code, bot_mode
    ))


@router.get("/agent-call-records/facets")
async def get_agent_facets(request: Request, session=Depends(console_session)):
    return await request.app.state.agent_report_client.get_facets()


@router.get("/agent-call-records/{record_id}")
async def get_agent_call_record(
    record_id: int,
    request: Request,
    session=Depends(console_session),
):
    return await request.app.state.agent_report_client.get_record(record_id)
