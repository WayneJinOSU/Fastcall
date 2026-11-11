import copy
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from robot_config_service.application.publication import PublicationUseCase
from robot_config_service.application.resolve import ResolveUseCase
from robot_config_service.application.robot_apps import RobotAppsUseCase
from robot_config_service.application.validation import ValidateConfigUseCase
from robot_config_service.capabilities.loader import CapabilityCatalog, load_catalog
from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.infrastructure.orm import Base, RobotAppVersionModel, RobotConfigAuditLogModel, RobotReleaseModel
from robot_config_service.infrastructure.unit_of_work import SqlAlchemyUnitOfWork

from tests.unit.robot_config_service.fixtures import valid_config


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for table in ("robot_app_version", "robot_config_audit_log"):
                connection.execute(text(f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(FAIL, 'immutable'); END"))
                connection.execute(text(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(FAIL, 'immutable'); END"))
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        self.validator = ValidateConfigUseCase(load_catalog())
        self.apps = RobotAppsUseCase(self.uow)
        self.publication = PublicationUseCase(self.uow, self.validator)
        self.app = self.apps.create("example-standard-en", "Standard English", valid_config(), "editor")

    def tearDown(self):
        self.uow.close()
        self.engine.dispose()

    def publish(self, revision=1):
        report = self.apps.validate(self.app["app_id"], revision, self.validator)
        return self.publication.publish(self.app["app_id"], revision, report["config_hash"], "publisher")

    def test_draft_optimistic_lock(self):
        with self.assertRaises(RobotConfigError) as caught:
            self.apps.update_draft(self.app["app_id"], 2, valid_config(), "editor")
        self.assertEqual(caught.exception.code, "DRAFT_REVISION_CONFLICT")
        self.assertEqual(self.apps.get(self.app["app_id"])["draft_revision"], 1)

    def test_publish_idempotency_diff_and_rollback(self):
        first = self.publish()
        second = self.publish()
        self.assertEqual(first["robot_app_version_id"], second["robot_app_version_id"])
        self.assertEqual(self.uow.session.query(RobotAppVersionModel).count(), 1)

        changed = valid_config()
        changed["prompt"]["system_template"] = "Changed {{user_name}}"
        self.apps.update_draft(self.app["app_id"], 1, changed, "editor")
        new_version = self.publish(revision=2)
        self.assertEqual(new_version["version_no"], 2)
        diff = self.publication.diff(self.app["app_id"], 1, 2)
        self.assertIn("Changed {{user_name}}", diff["unified_diff"])

        self.publication.rollback(self.app["app_id"], first["robot_app_version_id"], "operator")
        current = self.apps.get(self.app["app_id"])
        self.assertEqual(current["draft_revision"], 2)
        self.assertEqual(current["draft"]["prompt"]["system_template"], "Changed {{user_name}}")
        self.assertEqual(current["current_release"]["robot_app_version_id"], first["robot_app_version_id"])

    def test_publish_failure_rolls_back_all_writes(self):
        original_append = self.uow.audits.append

        def fail_on_publish(entry):
            if entry.action == "version.publish":
                raise RuntimeError("injected audit failure")
            original_append(entry)

        self.uow.audits.append = fail_on_publish
        report = self.apps.validate(self.app["app_id"], 1, self.validator)
        with self.assertRaises(RuntimeError):
            self.publication.publish(self.app["app_id"], 1, report["config_hash"], "publisher")
        self.assertEqual(self.uow.session.query(RobotAppVersionModel).count(), 0)
        self.assertEqual(self.uow.session.query(RobotReleaseModel).count(), 0)
        self.assertEqual(self.uow.session.query(RobotConfigAuditLogModel).filter_by(action="version.publish").count(), 0)

    def test_version_and_audit_are_immutable(self):
        published = self.publish()
        version = self.uow.session.get(RobotAppVersionModel, published["robot_app_version_id"])
        version.created_by = "other"
        with self.assertRaises(Exception):
            self.uow.session.commit()
        self.uow.session.rollback()
        audit = self.uow.session.query(RobotConfigAuditLogModel).first()
        with self.assertRaises(Exception):
            self.uow.session.delete(audit)
            self.uow.session.commit()
        self.uow.session.rollback()

    def test_disabled_capability_does_not_change_historical_resolve(self):
        published = self.publish()
        catalog_data = copy.deepcopy(load_catalog().data)
        catalog_data["runtime_profiles"][0]["enabled"] = False
        validator = ValidateConfigUseCase(CapabilityCatalog(catalog_data))
        changed = valid_config()
        changed["prompt"]["system_template"] = "Changed {{user_name}}"
        self.apps.update_draft(self.app["app_id"], 1, changed, "editor")
        report = self.apps.validate(self.app["app_id"], 2, validator)
        self.assertFalse(report["valid"])
        resolved = ResolveUseCase(self.uow).execute("example-standard-en")
        self.assertEqual(resolved["robot_app_version_id"], published["robot_app_version_id"])
        self.assertEqual(resolved["runtime_catalog_version"], "2026-11-10.1")

    def test_resolve_does_not_expose_missing_unpublished_or_disabled_state(self):
        for robot_code in ("missing", "example-standard-en"):
            with self.subTest(robot_code=robot_code):
                with self.assertRaises(RobotConfigError) as caught:
                    ResolveUseCase(self.uow).execute(robot_code)
                self.assertEqual(caught.exception.code, "RESOLVE_FAILED")
        self.publish()
        self.apps.set_enabled(self.app["app_id"], False, "operator")
        with self.assertRaises(RobotConfigError) as caught:
            ResolveUseCase(self.uow).execute("example-standard-en")
        self.assertEqual(caught.exception.code, "RESOLVE_FAILED")


if __name__ == "__main__":
    unittest.main()
