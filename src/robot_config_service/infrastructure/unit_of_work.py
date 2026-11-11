from robot_config_service.infrastructure.repositories import (
    SqlAlchemyAuditLogRepository,
    SqlAlchemyRobotAppRepository,
    SqlAlchemyRobotReleaseRepository,
    SqlAlchemyRobotVersionRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session):
        self.session = session
        self.apps = SqlAlchemyRobotAppRepository(session)
        self.versions = SqlAlchemyRobotVersionRepository(session)
        self.releases = SqlAlchemyRobotReleaseRepository(session)
        self.audits = SqlAlchemyAuditLogRepository(session)

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()

    def close(self):
        self.session.close()
