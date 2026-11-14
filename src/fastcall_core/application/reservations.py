from fastcall_core.application.call_results import FinalizeCallUseCase
from fastcall_core.application.dto import utcnow
from fastcall_core.domain.outbound_tasks import TaskStatus


class ReleaseUnauthorizedReservationUseCase:
    def __init__(self, uow, clock=utcnow):
        self.uow = uow
        self.clock = clock

    def execute(self, call_id, commit=True):
        call = self.uow.calls.get(call_id)
        task = self.uow.tasks.get(call.task_id)
        if task.status == TaskStatus.CANCELLED.value:
            return FinalizeCallUseCase(self.uow, self.clock).execute(
                call_id, "cancelled", "CANCELLED", "cancelled",
                commit=commit, expected_state_version=call.state_version,
                termination_reason="TASK_CANCELLED",
            )
        self.uow.calls.release_unauthorized_reservation(call_id, call.state_version, self.clock())
        if commit:
            self.uow.commit()
        return None
