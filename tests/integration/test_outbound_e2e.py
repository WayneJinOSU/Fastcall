import asyncio
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from robot_config_service.application.call_events import AgentSessionUseCase
from robot_config_service.application.dispatch import DispatchService
from robot_config_service.application.telephony_events import FreeSwitchEventService
from robot_config_service.application.webhooks import WebhookWorker
from robot_config_service.infrastructure.orm import Base, RobotAppModel, RobotAppVersionModel
from robot_config_service.infrastructure.outbound_orm import WebhookDeliveryModel
from robot_config_service.infrastructure.security import PhoneHmac, SecretCodec
from robot_config_service.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.robot_config_service.fixtures import valid_config
from tests.unit.robot_config_service.test_outbound_dispatch import FakeGateway
from tests.unit.robot_config_service.test_outbound_repositories import NOW, contact_record, task_record
from tests.unit.robot_config_service.test_outbound_webhooks import FakeSender


class OutboundEndToEndTests(unittest.TestCase):
    def test_call_lifecycle_through_webhook(self):
        async def scenario():
            engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
            Base.metadata.create_all(engine)
            sessions = sessionmaker(engine, expire_on_commit=False)
            factory = lambda: SqlAlchemyUnitOfWork(sessions())
            codec = SecretCodec({"v1": b"k" * 32}, "v1")
            hmac_keys = PhoneHmac({"v1": b"h" * 32}, "v1")
            uow = factory()
            uow.session.add(RobotAppModel(
                id="app_1", app_code="app", name="App", enabled=True, draft_json={},
                draft_revision=1, created_at=NOW, updated_at=NOW,
            ))
            uow.session.add(RobotAppVersionModel(
                id="version_1", app_id="app_1", version_no=1, source_draft_revision=1,
                content_json=valid_config(), config_hash="sha256:test",
                schema_version="robot-app/v1", runtime_engine="standard",
                runtime_adapter="deepseek_chat", runtime_catalog_version="test",
                validation_report_json={}, validated_at=NOW, created_by="test", created_at=NOW,
            ))
            task = task_record()
            task.callback_url = "https://crm.example/callback"
            task.callback_secret_ref = "crm"
            uow.tasks.add(task)
            contact = contact_record()
            contact.phone_ciphertext = codec.encrypt("+12025550101")
            contact.variables_ciphertext = codec.encrypt_json({"user_name": "Alice"})
            uow.contacts.add(contact)
            uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
            uow.commit()
            uow.close()

            gateway = FakeGateway()
            dispatcher = DispatchService(
                factory, gateway, codec, hmac_keys, clock=lambda: NOW, adapter_boot_id="boot-1"
            )
            self.assertEqual((await dispatcher.dispatch("call_1"))["dispatch"], "started")
            events = factory()
            common = {
                "instance_id": "fs-1", "stream_epoch": "boot-1", "channel_uuid": "origin_1",
                "origination_uuid": "origin_1", "fastcall_call_id": "call_1", "occurred_at": NOW,
            }
            FreeSwitchEventService(events, clock=lambda: NOW).handle(
                **common, event_type="CHANNEL_ANSWER", event_sequence=1
            )
            session = AgentSessionUseCase(events, codec, clock=lambda: NOW)
            self.assertEqual(session.claim("call_1")["claim_generation"], 1)
            for action in ("attached", "ready"):
                session.handle(
                    "call_1", action, 1, "agent-test", NOW,
                    f"agent:call_1:1:{action}",
                )
            FreeSwitchEventService(events, clock=lambda: NOW).handle(
                **common, event_type="CHANNEL_DESTROY", event_sequence=2,
                raw_hangup_cause="NORMAL_CLEARING",
            )
            self.assertEqual(events.calls.get("call_1").status, "succeeded")
            self.assertEqual(events.contacts.get("contact_1").status, "succeeded")
            self.assertEqual(events.tasks.get("task_1").status, "completed")
            self.assertEqual(events.session.query(WebhookDeliveryModel).count(), 2)
            events.close()

            sender = FakeSender([204, 204])
            worker = WebhookWorker(
                factory, sender, {"crm": "secret"}, clock=lambda: NOW, owner="worker-test"
            )
            await worker.run_once()
            await worker.run_once()
            verify = factory()
            deliveries = verify.session.query(WebhookDeliveryModel).all()
            self.assertEqual({item.status for item in deliveries}, {"delivered"})
            self.assertEqual(len(gateway.originate_calls), 1)
            verify.close()
            engine.dispose()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
