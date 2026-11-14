from fastcall_core.infrastructure.outbound_unit_of_work import SqlAlchemyOutboundUnitOfWork
from fastcall.infrastructure.event_worker_repository import SqlAlchemyEventWorkerRepository
from fastcall.infrastructure.query_repository import SqlAlchemyOutboundQueryRepository
from fastcall.infrastructure.repositories import (
    SqlAlchemyAuditLogRepository,
    SqlAlchemyRobotAppRepository,
    SqlAlchemyRobotReleaseRepository,
    SqlAlchemyRobotVersionRepository,
)
from fastcall.infrastructure.scheduler_repository import (
    SqlAlchemySchedulerRepository,
    SqlAlchemySchedulingCallRepository,
    SqlAlchemySchedulingContactRepository,
)
from fastcall.infrastructure.task_control_repository import SqlAlchemyTaskControlRepository
from fastcall.infrastructure.webhook_repository import SqlAlchemyWebhookWorkerRepository


class SqlAlchemyUnitOfWork(SqlAlchemyOutboundUnitOfWork):
    def __init__(self, session, result_changed_enabled=None):
        super().__init__(session, result_changed_enabled)
        self.contacts = SqlAlchemySchedulingContactRepository(session)
        self.calls = SqlAlchemySchedulingCallRepository(session)
        self.apps = SqlAlchemyRobotAppRepository(session)
        self.versions = SqlAlchemyRobotVersionRepository(session)
        self.releases = SqlAlchemyRobotReleaseRepository(session)
        self.audits = SqlAlchemyAuditLogRepository(session)
        self.scheduling = SqlAlchemySchedulerRepository(session)
        self.queries = SqlAlchemyOutboundQueryRepository(session)
        self.task_control = SqlAlchemyTaskControlRepository(session)
        self.webhook_worker = SqlAlchemyWebhookWorkerRepository(session)
        self.event_worker = SqlAlchemyEventWorkerRepository(session)
