import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from robot_config_service.application.publication import PublicationUseCase
from robot_config_service.application.robot_apps import RobotAppsUseCase
from robot_config_service.application.validation import ValidateConfigUseCase
from robot_config_service.capabilities.loader import load_catalog
from robot_config_service.application.outbound_ports import CallEventRecord
from robot_config_service.infrastructure.orm import RobotAppVersionModel, RobotConfigAuditLogModel
from robot_config_service.infrastructure.outbound_orm import CallEventModel
from robot_config_service.application.outbound_tasks import OutboundTasksUseCase
from robot_config_service.infrastructure.outbound_unit_of_work import SqlAlchemyOutboundUnitOfWork
from robot_config_service.infrastructure.security import PhoneHmac, SecretCodec
from robot_config_service.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.robot_config_service.fixtures import valid_config
from tests.unit.robot_config_service.test_outbound_repositories import (
    NOW,
    contact_record,
    task_record,
)


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
                "scheduler_state", "outbound_call_analysis", "call_analysis",
                "webhook_delivery", "call_blacklist", "call_event",
                "outbound_call", "task_contact", "outbound_task",
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

    def test_outbound_reservation_and_concurrent_event_deduplication(self):
        report = self.apps.validate(self.app["app_id"], 1, self.validator)
        published = PublicationUseCase(self.uow, self.validator).publish(
            self.app["app_id"], 1, report["config_hash"], "integration"
        )
        self.uow.commit()
        outbound = SqlAlchemyOutboundUnitOfWork(self.sessions())
        task = task_record()
        task.robot_app_id = self.app["app_id"]
        task.robot_version_id = published["robot_app_version_id"]
        task.config_hash = published["config_hash"]
        outbound.tasks.add(task)
        outbound.contacts.add(contact_record())
        outbound.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
        outbound.commit()

        def enqueue(index):
            uow = SqlAlchemyOutboundUnitOfWork(self.sessions())
            try:
                inserted = uow.call_events.enqueue(CallEventRecord(
                    id=f"event_row_{index}", source="agent",
                    event_id="agent:call_1:1:ready", event_type="session.ready",
                    task_id="task_1", contact_id="contact_1", call_id="call_1",
                    occurred_at=NOW, received_at=NOW, payload_json={"ready": True},
                    payload_hash="a" * 64, process_status="pending", process_owner=None,
                    process_lease_until=None, process_attempts=0, next_process_at=NOW,
                    processed_at=None, last_error=None,
                ))
                uow.commit()
                return inserted
            finally:
                uow.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(enqueue, range(2)))
        self.assertEqual(sorted(results), [False, True])
        outbound.session.rollback()
        self.assertEqual(outbound.session.query(CallEventModel).count(), 1)
        outbound.calls.release_unauthorized_reservation("call_1", 1, NOW)
        outbound.commit()
        self.assertEqual(outbound.contacts.get("contact_1").status, "pending")
        self.assertEqual(outbound.calls.count_active(), 0)
        outbound.close()

    def test_outbound_task_and_encrypted_contacts_on_mysql(self):
        report = self.apps.validate(self.app["app_id"], 1, self.validator)
        PublicationUseCase(self.uow, self.validator).publish(
            self.app["app_id"], 1, report["config_hash"], "integration"
        )
        use_case = OutboundTasksUseCase(
            self.uow, SecretCodec({"v1": b"k" * 32}, "v1"),
            PhoneHmac({"v1": b"h" * 32}, "v1"),
        )
        task = use_case.create("mysql-task", {
            "name": "MySQL", "robot_code": "example-standard-en", "timezone": "UTC",
            "default_country_code": "1", "scheduled_start_at": None,
            "scheduled_end_at": None, "calling_windows": [], "excluded_dates": [],
            "max_concurrent_calls": 2, "calls_per_minute": 30,
            "retry_policy": {"max_retries": 0, "retry_intervals_sec": [], "retry_on": []},
            "callback_url": None, "callback_secret_ref": None,
        })
        imported = use_case.import_contacts(task["task_id"], [{
            "customer_contact_id": "c1", "phone": "+12025550101",
            "variables": {"user_name": "Alice"},
        }])
        self.assertEqual(len(imported["accepted_contacts"]), 1)
        started = use_case.start(task["task_id"], 1)
        self.assertEqual(started["status"], "running")
        paused = use_case.pause(task["task_id"], started["state_version"])
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(use_case.resume(task["task_id"], paused["state_version"])["status"], "running")

    def test_outbound_migration_downgrade_and_upgrade(self):
        self.uow.session.execute(text(
            "CREATE TABLE call_analysis ("
            "id BIGINT PRIMARY KEY AUTO_INCREMENT, call_uuid VARCHAR(128), "
            "analysis_type VARCHAR(64), result_json LONGTEXT)"
        ))
        self.uow.commit()
        config = Config("deploy/robot_config_alembic.ini")
        with patch.dict(os.environ, {"ROBOT_CONFIG_DATABASE_URL": self.database_url}):
            command.downgrade(config, "0002")
            command.upgrade(config, "head")
        self.uow.session.rollback()
        self.assertEqual(self.uow.session.query(RobotAppVersionModel).count(), 0)
        tables = set(inspect(self.engine).get_table_names())
        self.assertIn("call_analysis", tables)
        self.assertIn("outbound_call_analysis", tables)


if __name__ == "__main__":
    unittest.main()
