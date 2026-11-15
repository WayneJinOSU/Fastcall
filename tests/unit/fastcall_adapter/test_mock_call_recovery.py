import asyncio
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastcall_adapter.application.mock_call_media import MockCallMediaRuntime, _MockSession
from fastcall_adapter.infrastructure.uow import SqlAlchemyAdapterUnitOfWork
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall_core.infrastructure.orm import Base
from tests.unit.fastcall.test_outbound_repositories import NOW, contact_record, task_record


class RecoveryGateway:
    def __init__(self):
        self.kills = []

    async def kill_channel(self, uuid, cause):
        self.kills.append((uuid, cause))


class RecoveryBrowser:
    def __init__(self):
        self.closed = False

    async def send_text(self, value):
        pass

    async def close(self):
        self.closed = True


class MockCallRecoveryTests(unittest.TestCase):
    def test_stale_call_is_finalized_and_releases_capacity(self):
        async def scenario():
            engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
            Base.metadata.create_all(engine)
            sessions = sessionmaker(engine, expire_on_commit=False)
            factory = lambda: SqlAlchemyAdapterUnitOfWork(sessions())
            uow = SqlAlchemyUnitOfWork(sessions())
            task = task_record()
            task.origin = "mock_call"
            uow.tasks.add(task)
            uow.contacts.add(contact_record())
            uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
            uow.commit()
            uow.close()
            gateway = RecoveryGateway()
            runtime = MockCallMediaRuntime(factory, gateway)
            await runtime.cleanup_stale()
            verify = factory()
            self.assertEqual(verify.calls.get("call_1").status, "failed")
            self.assertEqual(verify.contacts.get("contact_1").status, "failed")
            self.assertIsNone(verify.contacts.get("contact_1").active_call_id)
            self.assertEqual(verify.tasks.get("task_1").status, "completed")
            self.assertEqual(gateway.kills, [("origin_1", "NORMAL_CLEARING")])
            verify.close()
            engine.dispose()

        asyncio.run(scenario())

    def test_expired_ending_session_is_finalized_and_removed(self):
        async def scenario():
            engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
            Base.metadata.create_all(engine)
            sessions = sessionmaker(engine, expire_on_commit=False)
            factory = lambda: SqlAlchemyAdapterUnitOfWork(sessions())
            uow = SqlAlchemyUnitOfWork(sessions())
            task = task_record()
            task.origin = "mock_call"
            uow.tasks.add(task)
            uow.contacts.add(contact_record())
            uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
            uow.commit()
            uow.close()
            gateway = RecoveryGateway()
            runtime = MockCallMediaRuntime(factory, gateway)
            browser = RecoveryBrowser()
            session = _MockSession("task_1", browser)
            session.call_id = "call_1"
            session.ending = True
            session.ending_at = 0
            session.hangup_cause = "CALL_REJECTED"
            runtime._sessions["task_1"] = session
            runtime._by_call["call_1"] = session
            await runtime.cleanup_stale()
            verify = factory()
            self.assertEqual(verify.calls.get("call_1").result_code, "CALL_REJECTED")
            self.assertTrue(browser.closed)
            self.assertNotIn("task_1", runtime._sessions)
            self.assertNotIn("call_1", runtime._by_call)
            verify.close()
            engine.dispose()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
