from sqlalchemy import case, func, or_, select

from fastcall_core.application.outbound_ports import (
    OutboundCallRecord,
    OutboundTaskRecord,
    TaskContactRecord,
    WebhookDeliveryRecord,
)
from fastcall_core.domain.calls import ACTIVE_CALL_STATUSES, CallStatus
from fastcall_core.domain.outbound_tasks import TERMINAL_CONTACT_STATUSES
from fastcall_core.infrastructure.orm import RobotAppModel
from fastcall_core.infrastructure.outbound_orm import (
    OutboundCallModel,
    OutboundTaskModel,
    TaskContactModel,
    WebhookDeliveryModel,
)
from fastcall_core.infrastructure.outbound_repositories import _to_record


class SqlAlchemyOutboundQueryRepository:
    def __init__(self, session):
        self.session = session

    def all_contacts(self, task_id):
        models = self.session.execute(
            select(TaskContactModel).where(TaskContactModel.task_id == task_id)
        ).scalars().all()
        return [_to_record(TaskContactRecord, model) for model in models]

    def next_import_seq(self, task_id):
        current = self.session.execute(
            select(func.max(TaskContactModel.import_seq)).where(
                TaskContactModel.task_id == task_id
            )
        ).scalar()
        return (current or 0) + 1

    def list_tasks(
        self, *, status=None, robot_app_id=None, search=None, after=None, limit=50
    ):
        statement = select(OutboundTaskModel).where(OutboundTaskModel.origin == "outbound")
        if status:
            statement = statement.where(OutboundTaskModel.status == status)
        if robot_app_id:
            statement = statement.where(OutboundTaskModel.robot_app_id == robot_app_id)
        if search:
            statement = statement.where(or_(
                OutboundTaskModel.name.contains(search, autoescape=True),
                OutboundTaskModel.customer_task_id.contains(search, autoescape=True),
            ))
        if after:
            created_at, task_id = after
            statement = statement.where(or_(
                OutboundTaskModel.created_at < created_at,
                (OutboundTaskModel.created_at == created_at) & (OutboundTaskModel.id < task_id),
            ))
        models = self.session.execute(
            statement.order_by(OutboundTaskModel.created_at.desc(), OutboundTaskModel.id.desc())
            .limit(limit + 1)
        ).scalars().all()
        return [_to_record(OutboundTaskRecord, model) for model in models]

    def robot_summaries(self, app_ids):
        ids = [app_id for app_id in set(app_ids) if app_id]
        if not ids:
            return {}
        models = self.session.execute(
            select(RobotAppModel).where(RobotAppModel.id.in_(ids))
        ).scalars().all()
        return {
            model.id: {"robot_code": model.app_code, "robot_name": model.name}
            for model in models
        }

    def task_contact_counts(self, task_ids):
        if not task_ids:
            return {}
        rows = self.session.execute(
            select(TaskContactModel.task_id, TaskContactModel.status, func.count())
            .where(TaskContactModel.task_id.in_(task_ids))
            .group_by(TaskContactModel.task_id, TaskContactModel.status)
        ).all()
        result = {}
        for task_id, status, count in rows:
            result.setdefault(task_id, {})[status] = count
        return result

    def task_call_counts(self, task_ids):
        if not task_ids:
            return {}
        active = [status.value for status in ACTIVE_CALL_STATUSES]
        rows = self.session.execute(
            select(
                OutboundCallModel.task_id,
                func.sum(case((OutboundCallModel.status.in_(active), 1), else_=0)),
                func.sum(case((
                    OutboundCallModel.status == CallStatus.UNKNOWN.value, 1
                ), else_=0)),
            )
            .where(OutboundCallModel.task_id.in_(task_ids))
            .group_by(OutboundCallModel.task_id)
        ).all()
        return {
            task_id: {
                "active_call_count": int(active_count or 0),
                "unknown_call_count": int(unknown_count or 0),
            }
            for task_id, active_count, unknown_count in rows
        }

    def list_contacts(self, task_id, *, status=None, result_code=None, after=None, limit=50):
        statement = select(TaskContactModel).where(TaskContactModel.task_id == task_id)
        if status:
            statement = statement.where(TaskContactModel.status == status)
        if result_code:
            statement = statement.where(TaskContactModel.final_result_code == result_code)
        if after:
            import_seq, contact_id = after
            statement = statement.where(or_(
                TaskContactModel.import_seq > import_seq,
                (TaskContactModel.import_seq == import_seq) & (TaskContactModel.id > contact_id),
            ))
        models = self.session.execute(
            statement.order_by(TaskContactModel.import_seq, TaskContactModel.id).limit(limit + 1)
        ).scalars().all()
        return [_to_record(TaskContactRecord, model) for model in models]

    def list_calls(
        self, task_id=None, *, status=None, result_code=None, contact_id=None,
        after=None, limit=50,
    ):
        statement = select(OutboundCallModel)
        if task_id:
            statement = statement.where(OutboundCallModel.task_id == task_id)
        if status:
            statement = statement.where(OutboundCallModel.status == status)
        if result_code:
            statement = statement.where(OutboundCallModel.result_code == result_code)
        if contact_id:
            statement = statement.where(OutboundCallModel.contact_id == contact_id)
        if after:
            created_at, call_id = after
            statement = statement.where(or_(
                OutboundCallModel.created_at > created_at,
                (OutboundCallModel.created_at == created_at) & (OutboundCallModel.id > call_id),
            ))
        models = self.session.execute(
            statement.order_by(OutboundCallModel.created_at, OutboundCallModel.id).limit(limit + 1)
        ).scalars().all()
        return [_to_record(OutboundCallRecord, model) for model in models]

    def call_history_for_contacts(self, contact_ids):
        if not contact_ids:
            return {}
        models = self.session.execute(
            select(OutboundCallModel)
            .where(OutboundCallModel.contact_id.in_(contact_ids))
            .order_by(OutboundCallModel.contact_id, OutboundCallModel.call_no)
        ).scalars().all()
        result = {
            contact_id: {"call_count": 0, "last_call_id": None} for contact_id in contact_ids
        }
        for model in models:
            item = result[model.contact_id]
            item["last_call_id"] = model.id
            if model.dial_requested_at is not None:
                item["call_count"] += 1
        return result

    def list_cancellable_contacts(self, task_id):
        terminal = [status.value for status in TERMINAL_CONTACT_STATUSES]
        models = self.session.execute(
            select(TaskContactModel).where(
                TaskContactModel.task_id == task_id,
                TaskContactModel.status.not_in(terminal),
            )
        ).scalars().all()
        return [_to_record(TaskContactRecord, model) for model in models]

    def list_webhook_deliveries(self, task_id):
        models = self.session.execute(
            select(WebhookDeliveryModel)
            .where(WebhookDeliveryModel.task_id == task_id)
            .order_by(WebhookDeliveryModel.created_at, WebhookDeliveryModel.id)
        ).scalars().all()
        return [_to_record(WebhookDeliveryRecord, model) for model in models]

    def list_draft_mock_tasks(self):
        models = self.session.execute(
            select(OutboundTaskModel).where(
                OutboundTaskModel.origin == "mock_call",
                OutboundTaskModel.status == "draft",
            )
        ).scalars().all()
        return [_to_record(OutboundTaskRecord, model) for model in models]
