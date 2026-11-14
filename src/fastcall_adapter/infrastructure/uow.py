from fastcall_core.infrastructure.outbound_unit_of_work import SqlAlchemyOutboundUnitOfWork
from fastcall_adapter.infrastructure.hangup_repository import SqlAlchemyHangupRepository
from fastcall_adapter.infrastructure.dispatch_repository import SqlAlchemyDispatchRepository
from fastcall_adapter.infrastructure.telephony_repository import SqlAlchemyTelephonyRepository


class SqlAlchemyAdapterUnitOfWork(SqlAlchemyOutboundUnitOfWork):
    def __init__(self, session, result_changed_enabled=None):
        super().__init__(session, result_changed_enabled)
        self.dispatch = SqlAlchemyDispatchRepository(session)
        self.telephony = SqlAlchemyTelephonyRepository(session)
        self.hangup = SqlAlchemyHangupRepository(session)
