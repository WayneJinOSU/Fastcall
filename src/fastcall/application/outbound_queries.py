import base64
import datetime
import hashlib
import json

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


class OutboundQueryUseCase:
    def __init__(self, uow):
        self.uow = uow

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
