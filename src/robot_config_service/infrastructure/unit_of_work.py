from robot_config_service.infrastructure.outbound_unit_of_work import SqlAlchemyOutboundUnitOfWork
from robot_config_service.infrastructure.repositories import (
    SqlAlchemyAuditLogRepository,
    SqlAlchemyRobotAppRepository,
    SqlAlchemyRobotReleaseRepository,
    SqlAlchemyRobotVersionRepository,
)


class SqlAlchemyUnitOfWork(SqlAlchemyOutboundUnitOfWork):
    def __init__(self, session):
        super().__init__(session)
        self.apps = SqlAlchemyRobotAppRepository(session)
        self.versions = SqlAlchemyRobotVersionRepository(session)
        self.releases = SqlAlchemyRobotReleaseRepository(session)
        self.audits = SqlAlchemyAuditLogRepository(session)
