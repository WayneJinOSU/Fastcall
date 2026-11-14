import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastcall_adapter.infrastructure.uow import SqlAlchemyAdapterUnitOfWork
from fastcall_core.infrastructure.outbound_unit_of_work import SqlAlchemyOutboundUnitOfWork
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork


class UnitOfWorkCompositionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.sessions = sessionmaker(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_uow_and_dao_members_share_one_session(self):
        for uow_type in (
            SqlAlchemyOutboundUnitOfWork,
            SqlAlchemyUnitOfWork,
            SqlAlchemyAdapterUnitOfWork,
        ):
            with self.subTest(uow_type=uow_type.__name__):
                uow = uow_type(self.sessions())
                for member in vars(uow).values():
                    if hasattr(member, "session"):
                        self.assertIs(member.session, uow.session)
                uow.close()

    def test_specialized_workflows_do_not_leak_into_core(self):
        uow = SqlAlchemyOutboundUnitOfWork(self.sessions())
        for name in ("scheduling", "queries", "dispatch", "telephony", "webhook_worker"):
            self.assertFalse(hasattr(uow, name), name)
        uow.close()


if __name__ == "__main__":
    unittest.main()
