from sqlalchemy import func, or_, select

from robot_config_service.application.outbound_ports import OutboundCallRecord, TaskContactRecord
from robot_config_service.domain.outbound_tasks import TERMINAL_CONTACT_STATUSES
from robot_config_service.infrastructure.outbound_orm import OutboundCallModel, TaskContactModel
from robot_config_service.infrastructure.outbound_repositories import _to_record


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
