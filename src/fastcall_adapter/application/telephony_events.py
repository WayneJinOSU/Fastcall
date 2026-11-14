import hashlib
import json

from fastcall_core.application.call_results import FinalizeCallUseCase
from fastcall_core.application.dto import new_id, utcnow
from fastcall_core.application.outbound_ports import CallEventRecord
from fastcall_core.domain.calls import CallStatus
from fastcall_core.domain.telephony_results import SUCCESS_RESULTS, map_channel_hangup


class FreeSwitchEventService:
    def __init__(self, uow, clock=utcnow):
        self.uow = uow
        self.clock = clock

    def handle(
        self, *, instance_id, stream_epoch, event_type, event_sequence,
        channel_uuid, origination_uuid, fastcall_call_id,
        occurred_at, raw_hangup_cause=None,
    ):
        stable_parts = [
            instance_id, stream_epoch, channel_uuid, event_type,
            str(event_sequence) if event_sequence is not None else "",
        ]
        event_id = "freeswitch:" + ":".join(stable_parts)
        payload = {
            "event_type": event_type,
            "channel_uuid": channel_uuid,
            "origination_uuid": origination_uuid,
            "call_id": fastcall_call_id,
            "event_sequence": event_sequence,
            "raw_hangup_cause": raw_hangup_cause,
            "occurred_at": occurred_at.isoformat() + "Z",
        }
        if event_sequence is None:
            event_id = "freeswitch:hash:" + hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        now = self.clock()
        try:
            call_id = self.uow.telephony.resolve_call(
                channel_uuid, origination_uuid, fastcall_call_id
            )
            call = self.uow.calls.get(call_id)
            inserted = self.uow.call_events.enqueue(CallEventRecord(
                id=new_id("evt"), source="freeswitch", event_id=event_id,
                event_type=event_type, task_id=call.task_id, contact_id=call.contact_id,
                call_id=call_id, occurred_at=occurred_at, received_at=now,
                payload_json=payload, payload_hash=digest, process_status="processed",
                process_owner=None, process_lease_until=None, process_attempts=1,
                next_process_at=None, processed_at=now, last_error=None,
            ))
            if not inserted:
                self.uow.commit()
                return {"event_id": event_id, "ack": "duplicate"}
            if call.status in (
                CallStatus.SUCCEEDED.value, CallStatus.FAILED.value, CallStatus.CANCELLED.value
            ):
                self.uow.commit()
                return {"event_id": event_id, "ack": "terminal_duplicate"}
            call = self.uow.telephony.apply_channel_event(
                call_id, channel_uuid, event_type, raw_hangup_cause, now
            )
            if event_type in ("CHANNEL_DESTROY", "CHANNEL_HANGUP_COMPLETE"):
                result_code = map_channel_hangup(
                    raw_hangup_cause,
                    answered=call.answered_at is not None,
                    ready=call.session_ready_at is not None,
                )
                target = "succeeded" if result_code in SUCCESS_RESULTS else "failed"
                contact_target = "succeeded" if target == "succeeded" else "failed"
                FinalizeCallUseCase(self.uow, self.clock).execute(
                    call_id, target, result_code, contact_target,
                    raw_hangup_cause=raw_hangup_cause,
                    termination_reason="CHANNEL_HANGUP",
                    allow_retry=True,
                )
            else:
                self.uow.commit()
            return {"event_id": event_id, "ack": "processed", "call_id": call_id}
        except Exception:
            self.uow.rollback()
            raise
