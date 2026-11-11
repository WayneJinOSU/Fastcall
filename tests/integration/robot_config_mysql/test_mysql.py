import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from robot_config_service.application.publication import PublicationUseCase
from robot_config_service.application.robot_apps import RobotAppsUseCase
from robot_config_service.application.validation import ValidateConfigUseCase
from robot_config_service.capabilities.loader import load_catalog
from robot_config_service.infrastructure.orm import RobotAppVersionModel, RobotConfigAuditLogModel
from robot_config_service.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.robot_config_service.fixtures import valid_config


@unittest.skipUnless(
    os.getenv("ROBOT_CONFIG_TEST_DATABASE_URL"),
    "Requires a dedicated ROBOT_CONFIG_TEST_DATABASE_URL MySQL database",
)
class MySQLIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ["ROBOT_CONFIG_TEST_DATABASE_URL"]
        if not self.database_url.startswith("mysql+pymysql://"):
            raise RuntimeError("MySQL integration tests require a PyMySQL URL")
        self.engine = create_engine(self.database_url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self._drop_schema()
        config = Config("deploy/robot_config_alembic.ini")
        with patch.dict(os.environ, {"ROBOT_CONFIG_DATABASE_URL": self.database_url}):
            command.upgrade(config, "head")
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        self.validator = ValidateConfigUseCase(load_catalog())
        self.apps = RobotAppsUseCase(self.uow)
        self.app = self.apps.create(
            "example-standard-en", "Standard English", valid_config(), "integration"
        )

    def tearDown(self):
        if hasattr(self, "uow"):
            self.uow.close()
        if hasattr(self, "engine"):
            self._drop_schema()
            self.engine.dispose()

    def _drop_schema(self):
        with self.engine.begin() as connection:
            for table in ("robot_app_version", "robot_config_audit_log"):
                connection.execute(text(f"DROP TRIGGER IF EXISTS {table}_no_update"))
                connection.execute(text(f"DROP TRIGGER IF EXISTS {table}_no_delete"))
            for table in (
                "robot_config_audit_log", "robot_release", "robot_app_version",
                "robot_app", "robot_config_alembic_version",
            ):
                connection.execute(text(f"DROP TABLE IF EXISTS {table}"))

    def test_concurrent_publish_creates_one_version(self):
        report = self.apps.validate(self.app["app_id"], 1, self.validator)

        def publish():
            uow = SqlAlchemyUnitOfWork(self.sessions())
            try:
                return PublicationUseCase(uow, self.validator).publish(
                    self.app["app_id"], 1, report["config_hash"], "integration"
                )
            finally:
                uow.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: publish(), range(2)))
        self.assertEqual(results[0]["robot_app_version_id"], results[1]["robot_app_version_id"])
        self.uow.session.rollback()
        self.assertEqual(self.uow.session.query(RobotAppVersionModel).count(), 1)

    def test_triggers_reject_version_and_audit_mutation(self):
        report = self.apps.validate(self.app["app_id"], 1, self.validator)
        published = PublicationUseCase(self.uow, self.validator).publish(
            self.app["app_id"], 1, report["config_hash"], "integration"
        )
        version = self.uow.session.get(RobotAppVersionModel, published["robot_app_version_id"])
        version.created_by = "other"
        with self.assertRaises(Exception):
            self.uow.session.commit()
        self.uow.session.rollback()
        audit = self.uow.session.execute(
            select(RobotConfigAuditLogModel).where(
                RobotConfigAuditLogModel.action == "version.publish"
            )
        ).scalar_one()
        with self.assertRaises(Exception):
            self.uow.session.delete(audit)
            self.uow.session.commit()
        self.uow.session.rollback()

    def test_real_schema_startup_check_and_resolve(self):
        from robot_config_service.application.resolve import ResolveUseCase
        from robot_config_service.infrastructure.database import Database

        Database(self.engine, self.sessions).verify()
        report = self.apps.validate(self.app["app_id"], 1, self.validator)
        published = PublicationUseCase(self.uow, self.validator).publish(
            self.app["app_id"], 1, report["config_hash"], "integration"
        )
        resolved = ResolveUseCase(self.uow).execute("example-standard-en")
        self.assertEqual(resolved["robot_app_version_id"], published["robot_app_version_id"])
        self.assertEqual(resolved["config_hash"], report["config_hash"])
        self.assertEqual(resolved["app_config"], valid_config())


if __name__ == "__main__":
    unittest.main()
