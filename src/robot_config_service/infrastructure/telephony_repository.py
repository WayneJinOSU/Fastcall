from robot_config_service.domain.calls import CallStatus, validate_call_transition
from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.infrastructure.outbound_orm import OutboundCallModel
from robot_config_service.infrastructure.outbound_repositories import _compare_and_set
from robot_config_service.infrastructure.session_repository import SqlAlchemyAgentSessionRepository
from sqlalchemy import select


class SqlAlchemyTelephonyRepository:
    def __init__(self, session):
        self.session = session
        self.aggregate = SqlAlchemyAgentSessionRepository(session)

    def resolve_call(self, channel_uuid, origination_uuid, fastcall_call_id):
        conditions = []
        if origination_uuid:
            conditions.append(OutboundCallModel.origination_uuid == origination_uuid)
        if channel_uuid:
            conditions.append(OutboundCallModel.freeswitch_uuid == channel_uuid)
        if not conditions:
            raise RobotConfigError("CHANNEL_NOT_ASSOCIATED", "No channel identifier was supplied")
        model = self.session.execute(
            select(OutboundCallModel).where(conditions[0])
        ).scalar_one_or_none()
        if model is None and len(conditions) > 1:
            model = self.session.execute(
                select(OutboundCallModel).where(conditions[1])
            ).scalar_one_or_none()
        if model is None:
            raise RobotConfigError("CHANNEL_NOT_ASSOCIATED", "Channel does not match a Fast-Call Call")
        if fastcall_call_id != model.id:
            raise RobotConfigError("CHANNEL_CALL_MISMATCH", "Channel fastcall_call_id does not match")
        if model.origination_uuid != origination_uuid:
            raise RobotConfigError("CHANNEL_CALL_MISMATCH", "Channel origination UUID does not match")
        if model.freeswitch_uuid and channel_uuid and model.freeswitch_uuid != channel_uuid:
            raise RobotConfigError("CHANNEL_UUID_CONFLICT", "Channel UUID changed during the Call")
        return model.id

    def restore_active(self, call_id, expected_state_version, now):
        task, contact, call = self.aggregate._lock_aggregate(call_id)
        if call.state_version != expected_state_version:
            raise RobotConfigError("STATE_VERSION_CONFLICT", "Call state version does not match")
        if call.status != CallStatus.CHECKING.value or contact.status != "calling":
            raise RobotConfigError("CALL_NOT_RESTORABLE", "Only a checking Call can be restored")
        if call.answered_at is None or not call.freeswitch_uuid:
            raise RobotConfigError("CALL_NOT_RESTORABLE", "An answered active channel is required")
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call_id,
            expected_state_version,
            {"status": CallStatus.TALKING.value if call.session_ready_at else CallStatus.ANSWERED.value},
            (OutboundCallModel.status == CallStatus.CHECKING.value,),
        )
        self.session.flush()
        return self.aggregate.calls.get(call_id)

    def apply_channel_event(self, call_id, channel_uuid, event_type, raw_cause, now):
        task, contact, call = self.aggregate._lock_aggregate(call_id, allow_terminal_task=True)
        if call.freeswitch_uuid and call.freeswitch_uuid != channel_uuid:
            raise RobotConfigError("CHANNEL_UUID_CONFLICT", "Channel UUID changed during the Call")
        values = {"freeswitch_uuid": channel_uuid}
        target = None
        if event_type == "CHANNEL_PROGRESS" and call.status == CallStatus.DIALING.value:
            target = CallStatus.RINGING
            values["ringing_at"] = now
        elif event_type == "CHANNEL_ANSWER":
            if call.session_ready_at is not None:
                target = CallStatus.TALKING
            elif call.status in (
                CallStatus.DIALING.value, CallStatus.RINGING.value, CallStatus.CHECKING.value
            ):
                target = CallStatus.ANSWERED
            values["answered_at"] = call.answered_at or now
        elif event_type in ("CHANNEL_DESTROY", "CHANNEL_HANGUP_COMPLETE"):
            values["raw_hangup_cause"] = raw_cause
        if target is not None and target != call.status:
            values["status"] = validate_call_transition(call.status, target).value
        if event_type in ("CHANNEL_CREATE", "CHANNEL_PROGRESS", "CHANNEL_ANSWER") and call.dial_phase == "dial_requested":
            values["dial_phase"] = "acknowledged"
            values["dial_acknowledged_at"] = now
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call_id,
            call.state_version,
            values,
            (OutboundCallModel.status == call.status,),
        )
        self.session.flush()
        return self.aggregate.calls.get(call_id)
