import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field

from robot_config_service.application.call_results import FinalizeCallUseCase
from robot_config_service.application.dto import new_id, utcnow
from robot_config_service.application.outbound_ports import CallEventRecord
from robot_config_service.domain.errors import RobotConfigError


@dataclass
class _SendContext:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state: str = "NONE"


class DispatchService:
    def __init__(
        self, uow_factory, gateway, secret_codec, phone_hmac, *,
        global_max_concurrent=50, global_calls_per_minute=60, clock=utcnow,
        adapter_boot_id=None,
    ):
        self.uow_factory = uow_factory
        self.gateway = gateway
        self.secret_codec = secret_codec
        self.phone_hmac = phone_hmac
        self.global_max_concurrent = global_max_concurrent
        self.global_calls_per_minute = global_calls_per_minute
        self.clock = clock
        self.adapter_boot_id = adapter_boot_id or uuid.uuid4().hex
        self._contexts = {}
        self._contexts_lock = asyncio.Lock()

    async def _context(self, call_id):
        async with self._contexts_lock:
            return self._contexts.setdefault(call_id, _SendContext())

    def _hmac_versions(self, uow, call):
        contact = uow.contacts.get(call.contact_id)
        phone = self.secret_codec.decrypt(
            contact.phone_ciphertext, contact.encryption_key_version
        )
        return self.phone_hmac.all_versions(phone)

    async def dispatch(self, call_id):
        context = await self._context(call_id)
        async with context.lock:
            if context.state == "STARTED":
                return {"call_id": call_id, "dispatch": "already_started"}
            uow = self.uow_factory()
            try:
                call = uow.calls.get(call_id)
                if call is None:
                    raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
                if call.dial_requested_at is not None or call.status != "reserved":
                    return self._checking(uow, call_id)
                await asyncio.wait_for(self.gateway.health_check(), timeout=2)
                uow.dispatch.authorize(
                    call_id,
                    call.state_version,
                    self.adapter_boot_id,
                    self.clock(),
                    self._hmac_versions(uow, call),
                    self.global_max_concurrent,
                    self.global_calls_per_minute,
                )
                uow.commit()
            except RobotConfigError as error:
                uow.rollback()
                self._release_or_fail(uow, call_id, error.code)
                raise
            except Exception:
                uow.rollback()
                uow.close()
                return await self._after_unknown_commit(call_id, context)
            else:
                uow.close()
            return await self._originate_once(call_id, context)

    async def _after_unknown_commit(self, call_id, context):
        uow = self.uow_factory()
        try:
            call = uow.calls.get(call_id)
            if call is None:
                raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
            if call.dial_requested_at is None and call.status == "reserved":
                uow.calls.release_unauthorized_reservation(call_id, call.state_version, self.clock())
                uow.commit()
                return {"call_id": call_id, "dispatch": "not_authorized"}
            if call.adapter_boot_id == self.adapter_boot_id and context.state == "NONE":
                uow.close()
                return await self._originate_once(call_id, context)
            return self._checking(uow, call_id)
        finally:
            if not uow.session.is_active:
                uow.rollback()
            uow.close()

    def _checking(self, uow, call_id):
        try:
            uow.dispatch.mark_checking(call_id, self.clock())
            uow.commit()
            return {"call_id": call_id, "dispatch": "checking"}
        finally:
            uow.close()

    def _release_or_fail(self, uow, call_id, error_code):
        try:
            call = uow.calls.get(call_id)
            if call and call.status == "reserved" and call.dial_requested_at is None:
                if error_code in ("CALL_BLACKLISTED", "ROBOT_DISABLED"):
                    FinalizeCallUseCase(uow, self.clock).execute(
                        call_id, "failed", error_code, "failed",
                        failure_code=error_code, termination_reason="DIAL_PRECHECK_FAILED",
                    )
                else:
                    uow.calls.release_unauthorized_reservation(call_id, call.state_version, self.clock())
                    uow.commit()
        except Exception:
            uow.rollback()
        finally:
            uow.close()

    async def _originate_once(self, call_id, context):
        if context.state == "STARTED":
            return {"call_id": call_id, "dispatch": "already_started"}
        context.state = "STARTED"
        uow = self.uow_factory()
        try:
            call = uow.calls.get(call_id)
            contact = uow.contacts.get(call.contact_id)
            callee = self.secret_codec.decrypt(
                contact.phone_ciphertext, contact.encryption_key_version
            )
            response = await self.gateway.originate(
                call_id=call.id,
                origination_uuid=call.origination_uuid,
                callee=callee,
                task_id=call.task_id,
                contact_id=call.contact_id,
            )
            now = self.clock()
            payload = {"call_id": call_id, "job_uuid": response.get("job_uuid")}
            uow.call_events.enqueue(CallEventRecord(
                id=new_id("evt"), source="adapter",
                event_id=f"adapter:{self.adapter_boot_id}:{call_id}:originate",
                event_type="command.originate", task_id=call.task_id,
                contact_id=call.contact_id, call_id=call_id, occurred_at=now,
                received_at=now, payload_json=payload,
                payload_hash=hashlib.sha256(json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")).hexdigest(),
                process_status="processed", process_owner=None, process_lease_until=None,
                process_attempts=1, next_process_at=None, processed_at=now, last_error=None,
            ))
            uow.commit()
            uow.dispatch.acknowledge(
                call_id, response.get("uuid") or call.origination_uuid, self.clock()
            )
            uow.commit()
            return {"call_id": call_id, "dispatch": "started", "job_uuid": response.get("job_uuid")}
        except Exception:
            uow.rollback()
            uow.close()
            checking_uow = self.uow_factory()
            return self._checking(checking_uow, call_id)
        finally:
            if not uow.session.is_active:
                uow.rollback()
            uow.close()

    def recover_unacknowledged(self):
        uow = self.uow_factory()
        try:
            recovered = []
            for call in uow.dispatch.list_unacknowledged():
                uow.dispatch.mark_checking(call.id, self.clock())
                recovered.append(call.id)
            uow.commit()
            return recovered
        finally:
            uow.close()
