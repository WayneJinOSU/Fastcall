import asyncio
import uuid

from fastcall_core.application.dto import utcnow
from fastcall_core.domain.calls import CallStatus
from fastcall_core.domain.errors import RobotConfigError


TERMINAL_HANGUP_STATUSES = {
    CallStatus.SUCCEEDED.value,
    CallStatus.FAILED.value,
    CallStatus.CANCELLED.value,
}


class HangupCallUseCase:
    def __init__(self, uow, gateway):
        self.uow = uow
        self.gateway = gateway

    async def execute(self, call_id):
        call = self.uow.calls.get(call_id)
        if call is None:
            raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
        if call.status in TERMINAL_HANGUP_STATUSES:
            return {"call_id": call_id, "hangup": "already_terminal"}
        if not call.freeswitch_uuid:
            raise RobotConfigError("CALL_CHANNEL_UNAVAILABLE", "Call has no active channel UUID")
        await asyncio.wait_for(
            self.gateway.kill_channel(call.freeswitch_uuid, "NORMAL_CLEARING"),
            timeout=10,
        )
        return {"call_id": call_id, "hangup": "requested"}


class ReconcileHangupsUseCase:
    def __init__(self, uow_factory, gateway, clock=utcnow, owner=None):
        self.uow_factory = uow_factory
        self.gateway = gateway
        self.clock = clock
        self.owner = owner or uuid.uuid4().hex

    async def run_once(self):
        uow = self.uow_factory()
        try:
            instructions = uow.hangup.claim_due(self.clock(), self.owner)
            uow.commit()
        finally:
            uow.close()

        results = []
        for instruction in instructions:
            outcome = self.uow_factory()
            try:
                call = outcome.calls.get(instruction.call_id)
                if call is None:
                    raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
                if call.status in TERMINAL_HANGUP_STATUSES:
                    outcome.hangup.mark_processed(instruction.id, self.owner, self.clock())
                    outcome.commit()
                    results.append((instruction.id, "processed"))
                    continue
                await HangupCallUseCase(outcome, self.gateway).execute(call.id)
                outcome.hangup.defer(instruction.id, self.owner, self.clock())
                outcome.commit()
                results.append((instruction.id, "waiting"))
            except Exception as error:
                outcome.rollback()
                outcome.hangup.defer(
                    instruction.id, self.owner, self.clock(), type(error).__name__
                )
                outcome.commit()
                results.append((instruction.id, "retrying"))
            finally:
                outcome.close()
        return results
