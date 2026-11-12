import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from robot_config_service.application.robot_apps import RobotAppsUseCase
from robot_config_service.application.validation import ValidateConfigUseCase
from robot_config_service.application.voice_test import (
    VoiceTestSessionStore,
    VoiceTestUseCase,
    build_voice_test_variables,
)
from robot_config_service.capabilities.loader import load_catalog
from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.infrastructure.orm import Base
from robot_config_service.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.robot_config_service.fixtures import valid_config


class VoiceTestVariableTests(unittest.TestCase):
    def test_defaults_samples_and_manual_values_share_one_precedence(self):
        values = build_voice_test_variables(
            [
                {"key": "app_type", "type": "string", "default": "Retail"},
                {"key": "user_name", "type": "string", "required": True},
                {"key": "remaining_amount", "type": "decimal"},
                {"key": "contact_time", "type": "datetime", "default": "2031-01-01"},
            ],
            {"app_type": "Manual App", "ignored": "not-delivered"},
        )
        self.assertEqual(values["app_type"], "Manual App")
        self.assertEqual(values["user_name"], "Test User")
        self.assertEqual(values["remaining_amount"], "128.50")
        self.assertEqual(values["contact_time"], "2031-01-01")
        self.assertNotIn("ignored", values)


class VoiceTestSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        self.app = RobotAppsUseCase(self.uow).create(
            "example-standard-en", "Standard English", valid_config(), "tester"
        )
        self.validator = ValidateConfigUseCase(load_catalog())
        self.store = VoiceTestSessionStore()

    def tearDown(self):
        self.uow.close()
        self.engine.dispose()

    def test_session_fixes_draft_and_supplies_required_sample(self):
        result = VoiceTestUseCase(self.uow, self.validator, self.store).create(
            self.app["app_id"], 1, {}, "tester"
        )
        self.assertEqual(result["runtime_engine"], "standard")
        self.assertEqual(result["variables"]["user_name"], "Test User")
        self.assertTrue(result["config_hash"].startswith("sha256:"))
        session = self.store.claim(result["session_id"], result["ticket"], "tester")
        payload = session.start_payload()
        self.assertNotIn("runtime_engine", payload["data"])
        self.assertNotIn("test", payload["data"])
        self.assertEqual(payload["data"]["config"], valid_config())
        self.assertEqual(payload["data"]["variables"]["user_name"], "Test User")
        with self.assertRaises(RobotConfigError):
            self.store.claim(result["session_id"], result["ticket"], "tester")


if __name__ == "__main__":
    unittest.main()
