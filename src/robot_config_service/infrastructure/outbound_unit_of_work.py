from robot_config_service.infrastructure.analysis_repository import SqlAlchemyCallAnalysisRepository
from robot_config_service.infrastructure.dispatch_repository import SqlAlchemyDispatchRepository
from robot_config_service.infrastructure.event_worker_repository import SqlAlchemyEventWorkerRepository
from robot_config_service.infrastructure.query_repository import SqlAlchemyOutboundQueryRepository
from robot_config_service.infrastructure.result_repository import SqlAlchemyCallResultRepository
from robot_config_service.infrastructure.scheduler_repository import SqlAlchemySchedulerRepository
from robot_config_service.infrastructure.task_control_repository import SqlAlchemyTaskControlRepository
from robot_config_service.infrastructure.telephony_repository import SqlAlchemyTelephonyRepository
from robot_config_service.infrastructure.webhook_repository import SqlAlchemyWebhookWorkerRepository
from robot_config_service.infrastructure.session_repository import SqlAlchemyAgentSessionRepository
from robot_config_service.infrastructure.outbound_repositories import (
    SqlAlchemyCallBlacklistRepository,
    SqlAlchemyCallEventRepository,
    SqlAlchemyOutboundCallRepository,
    SqlAlchemyOutboundTaskRepository,
    SqlAlchemySchedulerStateRepository,
    SqlAlchemyTaskContactRepository,
    SqlAlchemyWebhookDeliveryRepository,
)


class SqlAlchemyOutboundUnitOfWork:
    def __init__(self, session):
        self.session = session
        self.tasks = SqlAlchemyOutboundTaskRepository(session)
        self.contacts = SqlAlchemyTaskContactRepository(session)
        self.calls = SqlAlchemyOutboundCallRepository(session)
        self.call_events = SqlAlchemyCallEventRepository(session)
        self.webhook_deliveries = SqlAlchemyWebhookDeliveryRepository(session)
        self.blacklist = SqlAlchemyCallBlacklistRepository(session)
        self.scheduler_state = SqlAlchemySchedulerStateRepository(session)
        self.sessions = SqlAlchemyAgentSessionRepository(session)
        self.results = SqlAlchemyCallResultRepository(session)
        self.dispatch = SqlAlchemyDispatchRepository(session)
        self.telephony = SqlAlchemyTelephonyRepository(session)
        self.scheduling = SqlAlchemySchedulerRepository(session)
        self.queries = SqlAlchemyOutboundQueryRepository(session)
        self.task_control = SqlAlchemyTaskControlRepository(session)
        self.webhook_worker = SqlAlchemyWebhookWorkerRepository(session)
        self.event_worker = SqlAlchemyEventWorkerRepository(session)
        self.analysis = SqlAlchemyCallAnalysisRepository(session)

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()

    def close(self):
        self.session.close()
