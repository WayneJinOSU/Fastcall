import hashlib
import json

from fastcall_core.application.call_analysis import SaveCallAnalysisUseCase
from fastcall_core.application.call_results import FinalizeCallUseCase
from fastcall_core.application.dto import new_id, utcnow
from fastcall_core.application.outbound_ports import CallEventRecord
from fastcall_core.application.resolve_exact import ResolveExactVersionUseCase
from fastcall_core.domain.calls import CallStatus
from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.domain.outbound_tasks import ContactStatus


EVENT_NAMES = {
    "attached": "session.attached",
    "ready": "session.ready",
    "failed": "session.failed",
    "completed": "session.completed",
}


class AgentSessionUseCase:
    def __init__(self, uow, secret_codec, clock=utcnow):
        self.uow = uow
        self.secret_codec = secret_codec
        self.clock = clock

    def claim(self, call_id, recover=False):
        try:
            call = self.uow.sessions.claim(call_id, recover=recover)
            task = self.uow.tasks.get(call.task_id)
            contact = self.uow.contacts.get(call.contact_id)
            resolved = ResolveExactVersionUseCase(self.uow).execute(call.robot_version_id)
            if resolved["config_hash"] != call.config_hash:
                raise RobotConfigError("CONFIG_HASH_MISMATCH", "Pinned Robot version hash changed")
            result = {
                "call_id": call.id,
                "claim_generation": call.claim_generation,
                "start_payload": {
                    "action": "start",
                    "data": {
                        "callUUID": call.id,
                        "claimGeneration": call.claim_generation,
                        "callee": self.secret_codec.decrypt(
                            contact.phone_ciphertext, contact.encryption_key_version
                        ),
                        "config": resolved["app_config"],
                        "variables": self.secret_codec.decrypt_json(
                            contact.variables_ciphertext, contact.encryption_key_version
                        ),
                        "robotVersionId": call.robot_version_id,
                        "configHash": call.config_hash,
                    },
                },
            }
            self.uow.commit()
            return result
        except Exception:
            self.uow.rollback()
            raise

    def _record(self, call, action, generation, agent_version, occurred_at, event_id, now, detail=None):
        event_type = EVENT_NAMES[action]
        expected_id = f"agent:{call.id}:{generation}:{action}"
        if event_id != expected_id:
            raise RobotConfigError("EVENT_ID_MISMATCH", "Agent event ID is not canonical")
        event_detail = detail or {}
        if action == "completed":
            event_detail = {
                key: event_detail[key]
                for key in ("voicemail", "business_result") if key in event_detail
            }
        payload = {
            "event_id": event_id,
            "call_id": call.id,
            "claim_generation": generation,
            "config_hash": call.config_hash,
            "agent_version": agent_version,
            "occurred_at": occurred_at.isoformat() + "Z",
            "detail": event_detail,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        inserted = self.uow.call_events.enqueue(CallEventRecord(
            id=new_id("evt"),
            source="agent",
            event_id=event_id,
            event_type=event_type,
            task_id=call.task_id,
            contact_id=call.contact_id,
            call_id=call.id,
            occurred_at=occurred_at,
            received_at=now,
            payload_json=payload,
            payload_hash=digest,
            process_status="processed",
            process_owner=None,
            process_lease_until=None,
            process_attempts=1,
            next_process_at=None,
            processed_at=now,
            last_error=None,
        ))
        return inserted

    def handle(self, call_id, action, generation, agent_version, occurred_at, event_id, detail=None):
        now = self.clock()
        try:
            if action not in EVENT_NAMES:
                raise RobotConfigError("UNKNOWN_SESSION_EVENT", "Unknown Agent Session event")
            if action == "failed":
                existing = self.uow.calls.get(call_id)
                if existing is not None and existing.session_state == "failed":
                    if not self._record(
                        existing, action, generation, agent_version, occurred_at,
                        event_id, now, detail,
                    ):
                        self.uow.commit()
                        return {"event_id": event_id, "ack": "duplicate"}
                task, contact, call = self.uow.sessions._lock_aggregate(call_id)
                if generation < 1 or generation > call.claim_generation:
                    raise RobotConfigError("STALE_CLAIM_GENERATION", "Claim generation is not known")
                if call.session_ready_at is not None and generation != call.claim_generation:
                    raise RobotConfigError("STALE_CLAIM_GENERATION", "Claim generation is not current")
            else:
                call = self.uow.calls.get(call_id)
                if call is None:
                    raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
            inserted = self._record(
                call, action, generation, agent_version, occurred_at, event_id, now, detail
            )
            if not inserted:
                self.uow.commit()
                return {"event_id": event_id, "ack": "duplicate"}
            if action == "attached":
                self.uow.sessions.attach(call_id, generation, call.config_hash)
            elif action == "ready":
                self.uow.sessions.ready(call_id, generation, call.config_hash, now)
            elif action == "failed":
                FinalizeCallUseCase(self.uow, self.clock).execute(
                    call_id,
                    CallStatus.FAILED.value,
                    "AI_PROVIDER_FAILED",
                    ContactStatus.FAILED.value,
                    generation=generation,
                    failure_code="AI_PROVIDER_FAILED",
                    failure_message=(detail or {}).get("message"),
                    termination_reason="AGENT_SESSION_FAILED",
                    session_failed=True,
                )
            elif action == "completed":
                if call.session_ready_at is None:
                    raise RobotConfigError("SESSION_NOT_READY", "Session is not ready")
                if call.status in (
                    CallStatus.SUCCEEDED.value, CallStatus.FAILED.value, CallStatus.CANCELLED.value
                ):
                    if detail and ("business_result" in detail or "analysis" in detail):
                        SaveCallAnalysisUseCase(self.uow, self.clock).execute(
                            call_id, detail.get("business_result"), detail.get("analysis", {}),
                            commit=False,
                        )
                    self.uow.commit()
                    return {"event_id": event_id, "ack": "terminal_duplicate"}
                voicemail = bool((detail or {}).get("voicemail"))
                if detail and ("business_result" in detail or "analysis" in detail):
                    SaveCallAnalysisUseCase(self.uow, self.clock).execute(
                        call_id, detail.get("business_result"), detail.get("analysis", {}),
                        commit=False,
                    )
                FinalizeCallUseCase(self.uow, self.clock).execute(
                    call_id,
                    CallStatus.SUCCEEDED.value,
                    "VOICEMAIL" if voicemail else "USER_HANGUP",
                    ContactStatus.SUCCEEDED.value,
                    generation=generation,
                    termination_reason="VOICEMAIL" if voicemail else "AGENT_COMPLETED",
                    answered_by_human=False if voicemail else None,
                )
            if action not in ("failed", "completed"):
                self.uow.commit()
            return {"event_id": event_id, "ack": "processed"}
        except Exception:
            self.uow.rollback()
            raise
