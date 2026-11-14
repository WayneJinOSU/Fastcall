from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.domain.outbound_tasks import ContactStatus, validate_contact_transition
from fastcall_core.infrastructure.outbound_orm import TaskContactModel
from fastcall_core.infrastructure.outbound_repositories import (
    SqlAlchemyTaskContactRepository,
    _compare_and_set,
)


class SqlAlchemyTaskControlRepository:
    def __init__(self, session):
        self.session = session
        self.contacts = SqlAlchemyTaskContactRepository(session)

    def cancel_pending_contact(self, contact_id, now):
        contact = self.contacts.get_for_update(contact_id)
        if contact is None:
            raise RobotConfigError("TASK_CONTACT_NOT_FOUND", "Task contact not found")
        if contact.active_call_id is not None:
            raise RobotConfigError("CONTACT_HAS_ACTIVE_CALL", "Contact has an active Call")
        validate_contact_transition(contact.status, ContactStatus.CANCELLED)
        _compare_and_set(
            self.session,
            TaskContactModel,
            contact_id,
            contact.state_version,
            {
                "status": ContactStatus.CANCELLED.value,
                "final_result_code": "CANCELLED",
                "result_revision": contact.result_revision + 1,
                "finished_at": now,
                "updated_at": now,
            },
            (
                TaskContactModel.status == contact.status,
                TaskContactModel.active_call_id.is_(None),
            ),
        )
        self.session.flush()
        return self.contacts.get(contact_id)
