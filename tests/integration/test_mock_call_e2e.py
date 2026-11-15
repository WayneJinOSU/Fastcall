import asyncio
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastcall.application.mock_calls import PrepareMockCallUseCase
from fastcall.application.publication import PublicationUseCase
from fastcall.application.robot_apps import RobotAppsUseCase
from fastcall.application.scheduler import DialScheduler
from fastcall.application.validation import ValidateConfigUseCase
from fastcall.capabilities.loader import load_catalog
from fastcall.infrastructure.security import PhoneHmac, SecretCodec
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall_adapter.application.dispatch import DispatchService
from fastcall_adapter.application.mock_call_media import MockCallMediaRuntime, _MockSession
from fastcall_adapter.application.telephony_events import FreeSwitchEventService
from fastcall_adapter.infrastructure.uow import SqlAlchemyAdapterUnitOfWork
from fastcall_core.application.call_events import AgentSessionUseCase
from fastcall_core.infrastructure.orm import Base
from fastcall_core.infrastructure.outbound_orm import WebhookDeliveryModel
from tests.unit.fastcall.fixtures import valid_config
from tests.unit.fastcall.test_outbound_repositories import NOW


class FakeGateway:
    def __init__(self):
        self.mock_calls = []
        self.answers = []

    async def health_check(self, capability=None):
        return True

    async def originate_mock(self, call_id, origination_uuid, task_id, contact_id):
        self.mock_calls.append(call_id)
        return {"uuid": origination_uuid, "job_uuid": "job-1"}

    async def answer_channel(self, uuid):
        self.answers.append(uuid)

    async def kill_channel(self, uuid, cause):
        pass


class FakeBrowser:
    def __init__(self):
        self.events = []

    async def send_text(self, value):
        self.events.append(value)


class MockCallEndToEndTests(unittest.TestCase):
    def test_scheduler_answer_agent_and_terminal_outbox(self):
        async def scenario():
            engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
            Base.metadata.create_all(engine)
            sessions = sessionmaker(engine, expire_on_commit=False)
            factory = lambda: SqlAlchemyUnitOfWork(sessions())
            adapter_factory = lambda: SqlAlchemyAdapterUnitOfWork(sessions())
            codec = SecretCodec({"v1": b"k" * 32}, "v1")
            hmac_keys = PhoneHmac({"v1": b"h" * 32}, "v1")
            setup = factory()
            app = RobotAppsUseCase(setup).create(
                "example-standard-en", "Standard English", valid_config(), "tester"
            )
            validator = ValidateConfigUseCase(load_catalog())
            version = PublicationUseCase(setup, validator).publish(
                app["app_id"], 1, validator.execute(valid_config()).config_hash, "tester"
            )
            task = PrepareMockCallUseCase(
                setup, codec, hmac_keys,
                callback_url="https://mock.example/callback", callback_secret_ref="mock",
                clock=lambda: NOW,
            ).prepare(
                app["app_id"], version["robot_app_version_id"], {"user_name": "Alice"},
                "mock:e2e-1", "tester",
            )
            setup.close()

            gateway = FakeGateway()
            mock_media = MockCallMediaRuntime(adapter_factory, gateway)
            mock_media._sessions[task["task_id"]] = _MockSession(task["task_id"], FakeBrowser())
            dispatcher = DispatchService(
                adapter_factory, gateway, codec, hmac_keys,
                clock=lambda: NOW, adapter_boot_id="boot-1", mock_calls=mock_media,
            )
            arm = factory()
            PrepareMockCallUseCase(
                arm, codec, hmac_keys, callback_url="https://mock.example/callback",
                callback_secret_ref="mock", clock=lambda: NOW,
            ).arm(task["task_id"], "tester")
            arm.close()
            scheduler = DialScheduler(
                factory, dispatcher, codec, hmac_keys, clock=lambda: NOW
            )
            created = await scheduler.scan_once()
            self.assertEqual(len(created), 1)
            self.assertEqual(gateway.mock_calls, created)
            call_id = created[0]

            await mock_media.handle_freeswitch({
                "Event-Name": "CHANNEL_CREATE", "Unique-ID": "browser-uuid",
                "variable_fastcall_task_id": task["task_id"],
                "variable_fastcall_call_id": call_id,
            })
            await mock_media.answer(task["task_id"])
            self.assertEqual(gateway.answers, ["browser-uuid"])

            events = adapter_factory()
            common = {
                "instance_id": "fs-1", "stream_epoch": "boot-1",
                "channel_uuid": "origin-1", "origination_uuid": "origin-1",
                "fastcall_call_id": call_id, "occurred_at": NOW,
            }
            call = events.calls.get(call_id)
            common["channel_uuid"] = call.origination_uuid
            common["origination_uuid"] = call.origination_uuid
            FreeSwitchEventService(events, clock=lambda: NOW).handle(
                **common, event_type="CHANNEL_ANSWER", event_sequence=1
            )
            session = AgentSessionUseCase(events, codec, clock=lambda: NOW)
            claim = session.claim(call_id)
            self.assertEqual(claim["start_payload"]["data"]["callSource"], "mock_call")
            for action in ("attached", "ready"):
                session.handle(
                    call_id, action, 1, "agent-test", NOW,
                    f"agent:{call_id}:1:{action}",
                )
            FreeSwitchEventService(events, clock=lambda: NOW).handle(
                **common, event_type="CHANNEL_DESTROY", event_sequence=2,
                raw_hangup_cause="NORMAL_CLEARING",
            )
            self.assertEqual(events.calls.get(call_id).status, "succeeded")
            self.assertEqual(events.calls.get(call_id).mock_browser_uuid, "browser-uuid")
            self.assertEqual(events.tasks.get(task["task_id"]).status, "completed")
            self.assertEqual(events.session.query(WebhookDeliveryModel).count(), 2)
            await mock_media.finish(call_id, "USER_HANGUP")
            events.close()
            engine.dispose()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
