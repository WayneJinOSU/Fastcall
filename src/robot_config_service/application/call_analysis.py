from robot_config_service.application.dto import new_id, utcnow
from robot_config_service.domain.errors import RobotConfigError


class SaveCallAnalysisUseCase:
    def __init__(self, uow, clock=utcnow):
        self.uow = uow
        self.clock = clock

    def execute(self, call_id, business_result, analysis, commit=True):
        call = self.uow.calls.get(call_id)
        if call is None:
            raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
        self.uow.analysis.upsert(
            new_id("analysis"), call_id, business_result, analysis, self.clock()
        )
        if commit:
            self.uow.commit()
        return {"call_id": call_id, "business_result": business_result}
