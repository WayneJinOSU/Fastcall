import asyncio
import hashlib
import json
import logging

from fastcall_core.application.call_results import FinalizeCallUseCase, _task_event_payload
from fastcall_core.application.dto import isoformat, new_id, utcnow
from fastcall_core.application.outbound_ports import CallEventRecord, WebhookDeliveryRecord
from fastcall_core.domain.outbound_tasks import TaskStatus
from fastcall_core.application.views import task_view


logger = logging.getLogger(__name__)


class CancelTaskUseCase:
    def __init__(self, uow, hangup_client=None, clock=utcnow):
        self.uow = uow
        self.hangup_client = hangup_client
        self.clock = clock

    async def execute(self, task_id, expected_state_version, hangup_active=True):
        now = self.clock()
        try:
            task = self.uow.tasks.transition_status(
                task_id, TaskStatus.CANCELLED.value, expected_state_version, now
            )
            contacts = self.uow.queries.list_cancellable_contacts(task_id)
            active_call_ids = []
            for contact in contacts:
                if contact.active_call_id:
                    call = self.uow.calls.get_for_update(contact.active_call_id)
                    if call.status == "reserved" and call.dial_requested_at is None:
                        FinalizeCallUseCase(self.uow, self.clock).execute(
                            call.id, "cancelled", "CANCELLED", "cancelled",
                            commit=False, revision_event=False,
                            expected_state_version=call.state_version,
                            termination_reason="TASK_CANCELLED",
                        )
                        continue
                    active_call_ids.append(call.id)
                    if hangup_active:
                        self._enqueue_hangup(task, contact, call.id, now)
                    continue
                contact = self.uow.task_control.cancel_pending_contact(contact.id, now)
                if task.callback_url:
                    history = self.uow.results.call_history(contact.id)
                    payload = {
                        "task_id": task.id,
                        "customer_task_id": task.customer_task_id,
                        "contact_id": contact.id,
                        "customer_contact_id": contact.customer_contact_id,
                        "status": contact.status,
                        "result_code": contact.final_result_code,
                        "result_revision": contact.result_revision,
                        **history,
                        "call_id": history["last_call_id"],
                        "finished_at": isoformat(contact.finished_at),
                    }
                    self.uow.webhook_deliveries.enqueue(WebhookDeliveryRecord(
                        id=new_id("wh"),
                        event_id=f"contact.completed:{contact.id}:{contact.result_revision}",
                        aggregate_type="contact", aggregate_id=contact.id,
                        aggregate_revision=contact.result_revision, task_id=task.id,
                        contact_id=contact.id, call_id=history["last_call_id"],
                        event_type="contact.completed", endpoint_url=task.callback_url,
                        payload_json=payload, status="pending", delivery_owner=None,
                        delivery_lease_until=None, attempt_count=0, next_delivery_at=now,
                        last_http_status=None, last_error=None, delivered_at=None, created_at=now,
                    ))
            if task.callback_url:
                counts = self.uow.results.contact_status_counts(task.id)
                self.uow.webhook_deliveries.enqueue(WebhookDeliveryRecord(
                    id=new_id("wh"), event_id=f"task.cancelled:{task.id}:{task.result_revision}",
                    aggregate_type="task", aggregate_id=task.id,
                    aggregate_revision=task.result_revision, task_id=task.id,
                    contact_id=None, call_id=None, event_type="task.cancelled",
                    endpoint_url=task.callback_url,
                    payload_json=_task_event_payload(task, counts), status="pending",
                    delivery_owner=None, delivery_lease_until=None, attempt_count=0,
                    next_delivery_at=now, last_http_status=None, last_error=None,
                    delivered_at=None, created_at=now,
                ))
            self.uow.commit()
        except Exception:
            self.uow.rollback()
            raise
        if hangup_active and self.hangup_client:
            await self._request_hangups(active_call_ids)
        return task_view(task)

    async def _request_hangups(self, call_ids):
        semaphore = asyncio.Semaphore(10)

        async def request(call_id):
            async with semaphore:
                try:
                    await self.hangup_client.hangup(call_id)
                except Exception as error:
                    logger.warning(
                        "Immediate hangup failed; reconciliation will retry call_id=%s error=%s",
                        call_id, type(error).__name__,
                    )

        tasks = [asyncio.create_task(request(call_id)) for call_id in call_ids]
        if not tasks:
            return
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        except asyncio.TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.warning("Immediate hangup batch timed out; reconciliation will retry")

    def _enqueue_hangup(self, task, contact, call_id, now):
        payload = {"task_id": task.id, "contact_id": contact.id, "call_id": call_id}
        payload_hash = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        self.uow.call_events.enqueue(CallEventRecord(
            id=new_id("evt"), source="fastcall",
            event_id=f"call.hangup_requested:{call_id}:{task.state_version}",
            event_type="call.hangup_requested", task_id=task.id,
            contact_id=contact.id, call_id=call_id, occurred_at=now,
            received_at=now, payload_json=payload, payload_hash=payload_hash,
            process_status="pending", process_owner=None, process_lease_until=None,
            process_attempts=0, next_process_at=now, processed_at=None, last_error=None,
        ))
