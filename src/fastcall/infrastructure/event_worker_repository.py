import datetime

from sqlalchemy import or_, select

from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.infrastructure.outbound_orm import CallEventModel


class SqlAlchemyEventWorkerRepository:
    def __init__(self, session):
        self.session = session

    def claim_due(self, now, owner, lease_seconds=30, limit=20):
        models = self.session.execute(
            select(CallEventModel)
            .where(
                or_(
                    CallEventModel.process_status.in_(["pending", "retrying"]),
                    (CallEventModel.process_status == "processing")
                    & (CallEventModel.process_lease_until <= now),
                ),
                or_(
                    CallEventModel.next_process_at.is_(None),
                    CallEventModel.next_process_at <= now,
                ),
                CallEventModel.event_type != "call.hangup_requested",
            )
            .order_by(CallEventModel.received_at, CallEventModel.id)
            .limit(limit)
            .with_for_update()
        ).scalars().all()
        for model in models:
            model.process_status = "processing"
            model.process_owner = owner
            model.process_lease_until = now + datetime.timedelta(seconds=lease_seconds)
            model.process_attempts += 1
        self.session.flush()
        return models

    def mark_processed(self, event_row_id, owner, now):
        model = self._owned(event_row_id, owner)
        model.process_status = "processed"
        model.processed_at = now
        model.process_owner = None
        model.process_lease_until = None
        model.last_error = None
        self.session.flush()

    def mark_retry(self, event_row_id, owner, error, now, max_attempts=10):
        model = self._owned(event_row_id, owner)
        model.last_error = error[:1024]
        model.process_owner = None
        model.process_lease_until = None
        if model.process_attempts >= max_attempts:
            model.process_status = "dead"
            model.next_process_at = None
        else:
            model.process_status = "retrying"
            model.next_process_at = now + datetime.timedelta(
                seconds=min(300, 2 ** model.process_attempts)
            )
        self.session.flush()

    def _owned(self, event_row_id, owner):
        model = self.session.get(CallEventModel, event_row_id)
        if model is None or model.process_owner != owner or model.process_status != "processing":
            raise RobotConfigError("EVENT_LEASE_LOST", "Call event lease is no longer owned")
        return model
