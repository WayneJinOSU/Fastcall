import hashlib
import json
import logging


logger = logging.getLogger(__name__)

from robot_config_service.application.call_results import FinalizeCallUseCase
from robot_config_service.application.dto import isoformat, new_id, utcnow
from robot_config_service.application.outbound_ports import CallEventRecord, WebhookDeliveryRecord
from robot_config_service.application.outbound_tasks import call_view
from robot_config_service.domain.errors import RobotConfigError


class CallCheckUseCase:
    def __init__(self, uow, gateway=None, clock=utcnow):
        self.uow = uow
        self.gateway = gateway
        self.clock = clock

    async def restore_answered_channels(self):
        if self.gateway is None:
            return []
        restored = []
        for call in self.uow.dispatch.list_checking():
            try:
                if (
                    call.answered_at is not None and call.freeswitch_uuid
                    and await self.gateway.channel_exists(call.freeswitch_uuid)
                ):
                    self.uow.telephony.restore_active(call.id, call.state_version, self.clock())
                    self.uow.commit()
                    restored.append(call.id)
            except Exception as error:
                self.uow.rollback()
                logger.warning("Call restore failed call_id=%s error=%s", call.id, type(error).__name__)
        if restored:
            self.uow.commit()
        return restored

    def expire_checks(self):
        now = self.clock()
        expired = []
        for call in self.uow.dispatch.list_expired_checks(now):
            try:
                FinalizeCallUseCase(self.uow, self.clock).execute(
                    call.id, "unknown", "CALL_CHECK_TIMEOUT", "unknown",
                    failure_code="CALL_CHECK_TIMEOUT", termination_reason="CHECK_DEADLINE_EXCEEDED",
                )
                expired.append(call.id)
            except Exception as error:
                self.uow.rollback()
                logger.warning("Call check failed call_id=%s error=%s", call.id, type(error).__name__)
        return expired

    async def review(self, call_id, request, actor):
        now = self.clock()
        call = self.uow.calls.get(call_id)
        if call is None:
            raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
        if call.status not in ("checking", "unknown"):
            raise RobotConfigError("CALL_NOT_REVIEWABLE", "Only checking or unknown Calls can be reviewed")
        if self.gateway is None:
            raise RobotConfigError(
                "CALL_REVIEW_UNAVAILABLE", "FreeSWITCH gateway is required for safe review"
            )
        if request["expected_state_version"] != call.state_version:
            raise RobotConfigError("STATE_VERSION_CONFLICT", "Call state version does not match")
        if self.gateway and call.freeswitch_uuid:
            active = await self.gateway.channel_exists(call.freeswitch_uuid)
            if active and request["resolution"] != "restore_active":
                if not request.get("force_release"):
                    raise RobotConfigError("CHANNEL_STILL_ACTIVE", "Active channel requires force_release")
                await self.gateway.kill_channel(call.freeswitch_uuid, "NORMAL_CLEARING")
                raise RobotConfigError(
                    "CHANNEL_STILL_ACTIVE", "Channel hangup was requested; review after its terminal event"
                )
        if request["resolution"] == "restore_active":
            if call.status != "checking" or not await self.gateway.channel_exists(call.freeswitch_uuid):
                raise RobotConfigError("CALL_NOT_RESTORABLE", "No active channel can be restored")
            call = self.uow.telephony.restore_active(
                call_id, request["expected_state_version"], now
            )
            self.uow.commit()
            return call_view(call)
        if request["resolution"] not in ("succeeded", "failed"):
            raise RobotConfigError("INVALID_REVIEW_RESOLUTION", "Unknown review resolution")
        if not request.get("result_code"):
            raise RobotConfigError("INVALID_REVIEW_RESOLUTION", "result_code is required")
        previous_result = call.result_code
        result = FinalizeCallUseCase(self.uow, self.clock).execute(
            call_id, request["resolution"], request["result_code"], request["resolution"],
            expected_state_version=request["expected_state_version"],
            reviewed_by=actor, review_evidence_ref=request["evidence_ref"],
            review_note=request["note"], termination_reason="MANUAL_REVIEW",
            commit=False, revision_event=False,
        )
        review_payload = {
            "call_id": call_id, "resolution": request["resolution"],
            "result_code": request["result_code"], "evidence_type": request["evidence_type"],
            "evidence_ref": request["evidence_ref"], "note": request["note"],
            "reviewed_by": actor, "reviewed_at": isoformat(now),
        }
        self.uow.call_events.enqueue(CallEventRecord(
            id=new_id("evt"), source="admin",
            event_id=f"review:{call_id}:{request['expected_state_version']}",
            event_type="call.reviewed", task_id=result["task"].id,
            contact_id=result["contact"].id, call_id=call_id, occurred_at=now,
            received_at=now, payload_json=review_payload,
            payload_hash=hashlib.sha256(json.dumps(
                review_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest(),
            process_status="processed", process_owner=None, process_lease_until=None,
            process_attempts=1, next_process_at=None, processed_at=now, last_error=None,
        ))
        contact = result["contact"]
        task = result["task"]
        if not result["contact_first_terminal"] and task.callback_url:
            history = self.uow.results.call_history(contact.id)
            payload = {
                "task_id": task.id, "customer_task_id": task.customer_task_id,
                "contact_id": contact.id, "customer_contact_id": contact.customer_contact_id,
                "status": contact.status, "result_code": contact.final_result_code,
                "previous_result_code": previous_result, "result_revision": contact.result_revision,
                **history, "call_id": call_id, "reason": request["note"],
                "evidence_ref": request["evidence_ref"], "changed_at": isoformat(now),
            }
            self.uow.webhook_deliveries.enqueue(WebhookDeliveryRecord(
                id=new_id("wh"), event_id=f"contact.result_changed:{contact.id}:{contact.result_revision}",
                aggregate_type="contact", aggregate_id=contact.id,
                aggregate_revision=contact.result_revision, task_id=task.id,
                contact_id=contact.id, call_id=call_id, event_type="contact.result_changed",
                endpoint_url=task.callback_url, payload_json=payload, status="pending",
                delivery_owner=None, delivery_lease_until=None, attempt_count=0,
                next_delivery_at=now, last_http_status=None, last_error=None,
                delivered_at=None, created_at=now,
            ))
        self.uow.commit()
        return call_view(result["call"])
