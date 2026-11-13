from robot_config_service.application.call_results import _task_event_payload
from robot_config_service.application.dto import isoformat, new_id, utcnow
from robot_config_service.application.outbound_ports import WebhookDeliveryRecord
from robot_config_service.application.outbound_tasks import task_view
from robot_config_service.domain.outbound_tasks import TaskStatus


class CancelTaskUseCase:
    def __init__(self, uow, gateway=None, clock=utcnow):
        self.uow = uow
        self.gateway = gateway
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
                    active_call_ids.append(contact.active_call_id)
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
        if hangup_active and self.gateway:
            for call_id in active_call_ids:
                call = self.uow.calls.get(call_id)
                if call and call.freeswitch_uuid:
                    await self.gateway.kill_channel(call.freeswitch_uuid, "NORMAL_CLEARING")
        return task_view(task)
