from robot_config_service.application.dto import isoformat, new_id, utcnow
from robot_config_service.application.outbound_ports import WebhookDeliveryRecord
from robot_config_service.domain.calling_windows import next_calling_window
from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.domain.outbound_tasks import TaskStatus
from robot_config_service.domain.retry_policy import RetryPolicy


def _contact_event_payload(result):
    task = result["task"]
    contact = result["contact"]
    call = result["call"]
    return {
        "task_id": task.id,
        "customer_task_id": task.customer_task_id,
        "contact_id": contact.id,
        "customer_contact_id": contact.customer_contact_id,
        "status": contact.status,
        "result_code": contact.final_result_code,
        "call_count": result["call_history"]["call_count"],
        "last_call_id": result["call_history"]["last_call_id"],
        "result_revision": contact.result_revision,
        "call_id": call.id,
        "finished_at": isoformat(contact.finished_at),
    }


def _task_event_payload(task, status_counts):
    return {
        "task_id": task.id,
        "customer_task_id": task.customer_task_id,
        "status": task.status,
        "result_revision": task.result_revision,
        "contact_statistics": status_counts,
        "completed_at": isoformat(task.completed_at),
    }


class CompleteTaskUseCase:
    def __init__(self, uow, clock=utcnow):
        self.uow = uow
        self.clock = clock

    def execute(self, task_id, now=None, commit=True):
        now = now or self.clock()
        task = self.uow.tasks.get_for_update(task_id)
        if task is None:
            raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Outbound task not found")
        if task.status != TaskStatus.RUNNING.value:
            return None
        if self.uow.results.count_unfinished_contacts(task.id) != 0:
            return None
        task = self.uow.results.complete_task(task, now)
        if task.callback_url:
            self.uow.webhook_deliveries.enqueue(WebhookDeliveryRecord(
                id=new_id("wh"),
                event_id=f"task.completed:{task.id}:{task.result_revision}",
                aggregate_type="task", aggregate_id=task.id,
                aggregate_revision=task.result_revision, task_id=task.id,
                contact_id=None, call_id=None, event_type="task.completed",
                endpoint_url=task.callback_url,
                payload_json=_task_event_payload(
                    task, self.uow.results.contact_status_counts(task.id)
                ),
                status="pending", delivery_owner=None, delivery_lease_until=None,
                attempt_count=0, next_delivery_at=now, last_http_status=None,
                last_error=None, delivered_at=None, created_at=now,
            ))
        if commit:
            self.uow.commit()
        return task


class FinalizeCallUseCase:
    def __init__(self, uow, clock=utcnow):
        self.uow = uow
        self.clock = clock

    def execute(self, call_id, target, result_code, contact_target, **options):
        now = options.pop("now", None) or self.clock()
        commit = options.pop("commit", True)
        revision_event = options.pop("revision_event", True)
        previous_call = self.uow.calls.get(call_id)
        previous_result = previous_call.result_code
        if options.pop("allow_retry", False):
            call = self.uow.calls.get(call_id)
            contact = self.uow.contacts.get(call.contact_id)
            task = self.uow.tasks.get(call.task_id)
            policy = RetryPolicy.from_dict(task.retry_policy_json)
            retry_at = policy.next_retry_at(result_code, contact.retry_count, now)
            if retry_at is not None:
                retry_at = next_calling_window(
                    retry_at, task.timezone, task.calling_windows_json, task.excluded_dates_json
                )
            if (
                retry_at is not None
                and task.status not in (TaskStatus.CANCELLED.value, TaskStatus.COMPLETED.value)
                and (task.scheduled_end_at is None or retry_at < task.scheduled_end_at)
            ):
                contact_target = "waiting_retry"
                options["retry_at"] = retry_at
        result = self.uow.results.finalize(
            call_id, target, result_code, contact_target, now, **options
        )
        result["call_history"] = self.uow.results.call_history(result["contact"].id)
        task = result["task"]
        contact = result["contact"]
        if result["contact_first_terminal"] and task.callback_url:
            revision = contact.result_revision
            self.uow.webhook_deliveries.enqueue(WebhookDeliveryRecord(
                id=new_id("wh"),
                event_id=f"contact.completed:{contact.id}:{revision}",
                aggregate_type="contact",
                aggregate_id=contact.id,
                aggregate_revision=revision,
                task_id=task.id,
                contact_id=contact.id,
                call_id=result["call"].id,
                event_type="contact.completed",
                endpoint_url=task.callback_url,
                payload_json=_contact_event_payload(result),
                status="pending",
                delivery_owner=None,
                delivery_lease_until=None,
                attempt_count=0,
                next_delivery_at=now,
                last_http_status=None,
                last_error=None,
                delivered_at=None,
                created_at=now,
            ))
        if (
            revision_event and not result["contact_first_terminal"]
            and contact.status in ("succeeded", "failed", "unknown", "cancelled")
            and previous_result != contact.final_result_code and task.callback_url
        ):
            payload = _contact_event_payload(result)
            payload.update({
                "previous_result_code": previous_result,
                "reason": options.get("termination_reason"),
                "evidence_ref": options.get("review_evidence_ref"),
                "changed_at": isoformat(now),
            })
            self.uow.webhook_deliveries.enqueue(WebhookDeliveryRecord(
                id=new_id("wh"),
                event_id=f"contact.result_changed:{contact.id}:{contact.result_revision}",
                aggregate_type="contact", aggregate_id=contact.id,
                aggregate_revision=contact.result_revision, task_id=task.id,
                contact_id=contact.id, call_id=result["call"].id,
                event_type="contact.result_changed", endpoint_url=task.callback_url,
                payload_json=payload, status="pending", delivery_owner=None,
                delivery_lease_until=None, attempt_count=0, next_delivery_at=now,
                last_http_status=None, last_error=None, delivered_at=None, created_at=now,
            ))
        completed = CompleteTaskUseCase(self.uow, self.clock).execute(
            task.id, now=now, commit=False
        )
        if completed is not None:
            result["task"] = completed
        if commit:
            self.uow.commit()
        return result
