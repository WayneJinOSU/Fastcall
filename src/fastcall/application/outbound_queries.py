import base64
import datetime
import json

from fastcall_core.application.dto import isoformat
from fastcall_core.application.views import call_view, contact_view
from fastcall_core.domain.errors import RobotConfigError


def _cursor(filters, after):
    raw = json.dumps({"filters": filters, "after": after}, sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor, filters):
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise RobotConfigError("INVALID_CURSOR", "Cursor is invalid") from error
    if value.get("filters") != filters:
        raise RobotConfigError("INVALID_CURSOR", "Cursor filters do not match the request")
    return value.get("after")


def _time(value):
    return isoformat(value) if value else None


def console_task_view(task, contact_statistics=None, call_statistics=None, robot=None):
    return {
        "task_id": task.id,
        "origin": task.origin,
        "customer_task_id": task.customer_task_id,
        "name": task.name,
        "robot_app_id": task.robot_app_id,
        "robot_version_id": task.robot_version_id,
        "config_hash": task.config_hash,
        "status": task.status,
        "health_status": task.health_status,
        "timezone": task.timezone,
        "scheduled_start_at": _time(task.scheduled_start_at),
        "scheduled_end_at": _time(task.scheduled_end_at),
        "calling_windows": task.calling_windows_json,
        "excluded_dates": task.excluded_dates_json,
        "max_concurrent_calls": task.max_concurrent_calls,
        "calls_per_minute": task.calls_per_minute,
        "retry_policy": task.retry_policy_json,
        "state_version": task.state_version,
        "last_error_code": task.last_error_code,
        "contact_statistics": contact_statistics or {},
        "active_call_count": (call_statistics or {}).get("active_call_count", 0),
        "unknown_call_count": (call_statistics or {}).get("unknown_call_count", 0),
        "created_at": _time(task.created_at),
        "started_at": _time(task.started_at),
        "completed_at": _time(task.completed_at),
        **(robot or {"robot_code": None, "robot_name": None}),
    }


def console_call_view(call):
    values = {
        "call_id": call.id,
        "task_id": call.task_id,
        "contact_id": call.contact_id,
        "call_no": call.call_no,
        "status": call.status,
        "result_code": call.result_code,
        "retryable": call.retryable,
        "robot_version_id": call.robot_version_id,
        "config_hash": call.config_hash,
        "origination_uuid": call.origination_uuid,
        "freeswitch_uuid": call.freeswitch_uuid,
        "claim_generation": call.claim_generation,
        "session_state": call.session_state,
        "session_ready_at": call.session_ready_at,
        "dial_phase": call.dial_phase,
        "answered_by_human": call.answered_by_human,
        "raw_hangup_cause": call.raw_hangup_cause,
        "hangup_source": call.hangup_source,
        "failure_code": call.failure_code,
        "termination_reason": call.termination_reason,
        "state_version": call.state_version,
        "created_at": call.created_at,
        "dial_requested_at": call.dial_requested_at,
        "dial_acknowledged_at": call.dial_acknowledged_at,
        "dialing_at": call.dialing_at,
        "ringing_at": call.ringing_at,
        "answered_at": call.answered_at,
        "conversation_started_at": call.conversation_started_at,
        "ended_at": call.ended_at,
        "duration_sec": call.duration_sec,
    }
    return {
        key: _time(value) if isinstance(value, datetime.datetime) else value
        for key, value in values.items()
    }


class OutboundQueryUseCase:
    def __init__(self, uow):
        self.uow = uow

    def list_tasks(self, status, robot_app_id, search, cursor, limit):
        filters = {
            "status": status or "", "robot_app_id": robot_app_id or "", "search": search or "",
        }
        after = _decode_cursor(cursor, filters)
        if after:
            after = [datetime.datetime.fromisoformat(after[0]), after[1]]
        records = self.uow.queries.list_tasks(
            status=status, robot_app_id=robot_app_id, search=search,
            after=after, limit=limit,
        )
        page = records[:limit]
        task_ids = [item.id for item in page]
        contact_counts = self.uow.queries.task_contact_counts(task_ids)
        call_counts = self.uow.queries.task_call_counts(task_ids)
        robots = self.uow.queries.robot_summaries([item.robot_app_id for item in page])
        next_cursor = None
        if len(records) > limit and page:
            last = page[-1]
            next_cursor = _cursor(filters, [last.created_at.isoformat(), last.id])
        return {
            "items": [
                console_task_view(
                    item, contact_counts.get(item.id), call_counts.get(item.id),
                    robots.get(item.robot_app_id),
                )
                for item in page
            ],
            "next_cursor": next_cursor,
        }

    def get_console_task(self, task_id):
        task = self.uow.tasks.get(task_id)
        if task is None:
            raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Outbound task not found")
        contacts = self.uow.results.contact_status_counts(task_id)
        calls = self.uow.queries.task_call_counts([task_id]).get(task_id)
        robot = self.uow.queries.robot_summaries([task.robot_app_id]).get(task.robot_app_id)
        return console_task_view(task, contacts, calls, robot)

    def list_contacts(self, task_id, status, result_code, cursor, limit):
        filters = {"task_id": task_id, "status": status, "result_code": result_code}
        after = _decode_cursor(cursor, filters)
        records = self.uow.queries.list_contacts(
            task_id, status=status, result_code=result_code, after=after, limit=limit
        )
        page = records[:limit]
        histories = self.uow.queries.call_history_for_contacts([item.id for item in page])
        next_cursor = None
        if len(records) > limit and page:
            last = page[-1]
            next_cursor = _cursor(filters, [last.import_seq, last.id])
        return {
            "items": [contact_view(item, histories[item.id]) for item in page],
            "next_cursor": next_cursor,
        }

    def list_calls(self, task_id, status, result_code, contact_id, cursor, limit):
        filters = {
            "task_id": task_id, "status": status,
            "result_code": result_code, "contact_id": contact_id,
        }
        after = _decode_cursor(cursor, filters)
        if after:
            after = [datetime.datetime.fromisoformat(after[0]), after[1]]
        records = self.uow.queries.list_calls(
            task_id, status=status, result_code=result_code, contact_id=contact_id,
            after=after, limit=limit,
        )
        page = records[:limit]
        next_cursor = None
        if len(records) > limit and page:
            last = page[-1]
            next_cursor = _cursor(filters, [last.created_at.isoformat(), last.id])
        return {"items": [call_view(item) for item in page], "next_cursor": next_cursor}

    def list_console_calls(self, task_id, status, result_code, contact_id, cursor, limit):
        filters = {
            "task_id": task_id, "status": status,
            "result_code": result_code, "contact_id": contact_id,
        }
        after = _decode_cursor(cursor, filters)
        if after:
            after = [datetime.datetime.fromisoformat(after[0]), after[1]]
        records = self.uow.queries.list_calls(
            task_id, status=status, result_code=result_code, contact_id=contact_id,
            after=after, limit=limit,
        )
        page = records[:limit]
        next_cursor = None
        if len(records) > limit and page:
            last = page[-1]
            next_cursor = _cursor(filters, [last.created_at.isoformat(), last.id])
        return {
            "items": [console_call_view(item) for item in page],
            "next_cursor": next_cursor,
        }

    def get_contact(self, contact_id):
        contact = self.uow.contacts.get(contact_id)
        if contact is None:
            raise RobotConfigError("TASK_CONTACT_NOT_FOUND", "Task contact not found")
        history = self.uow.results.call_history(contact_id)
        return contact_view(contact, history)

    def get_call(self, call_id):
        call = self.uow.calls.get(call_id)
        if call is None:
            raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
        return call_view(call)

    def get_console_call(self, call_id):
        call = self.uow.calls.get(call_id)
        if call is None:
            raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
        return console_call_view(call)
