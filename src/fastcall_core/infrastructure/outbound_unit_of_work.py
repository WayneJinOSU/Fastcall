from fastcall_core.infrastructure.analysis_repository import SqlAlchemyCallAnalysisRepository
from fastcall_core.infrastructure.outbound_repositories import (
    SqlAlchemyCallBlacklistRepository,
    SqlAlchemyCallEventRepository,
    SqlAlchemyOutboundCallRepository,
    SqlAlchemyOutboundTaskRepository,
    SqlAlchemySchedulerStateRepository,
    SqlAlchemyTaskContactRepository,
    SqlAlchemyWebhookDeliveryRepository,
)
from fastcall_core.infrastructure.pinned_version_repository import (
    SqlAlchemyPinnedVersionRepository,
)
from fastcall_core.infrastructure.result_repository import SqlAlchemyCallResultRepository
from fastcall_core.infrastructure.session_repository import SqlAlchemyAgentSessionRepository


class SqlAlchemyOutboundUnitOfWork:
    def __init__(self, session, result_changed_enabled=None):
        self.session = session
        self.result_changed_enabled = result_changed_enabled or (lambda: False)
        self.tasks = SqlAlchemyOutboundTaskRepository(session)
        self.contacts = SqlAlchemyTaskContactRepository(session)
        self.calls = SqlAlchemyOutboundCallRepository(session)
        self.call_events = SqlAlchemyCallEventRepository(session)
        self.webhook_deliveries = SqlAlchemyWebhookDeliveryRepository(session)
        self.blacklist = SqlAlchemyCallBlacklistRepository(session)
        self.scheduler_state = SqlAlchemySchedulerStateRepository(session)
        self.sessions = SqlAlchemyAgentSessionRepository(session)
        self.results = SqlAlchemyCallResultRepository(session)
        self.analysis = SqlAlchemyCallAnalysisRepository(session)
        self.pinned_versions = SqlAlchemyPinnedVersionRepository(session)

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()

    def close(self):
        self.session.close()
