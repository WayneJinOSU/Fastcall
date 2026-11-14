import datetime

from fastcall_core.application.dto import isoformat


def task_view(task, status_counts=None):
    return {
        "task_id": task.id,
        "customer_task_id": task.customer_task_id,
        "name": task.name,
        "robot_app_id": task.robot_app_id,
        "robot_version_id": task.robot_version_id,
        "config_hash": task.config_hash,
        "status": task.status,
        "health_status": task.health_status,
        "timezone": task.timezone,
        "default_country_code": task.default_country_code,
        "scheduled_start_at": isoformat(task.scheduled_start_at) if task.scheduled_start_at else None,
        "scheduled_end_at": isoformat(task.scheduled_end_at) if task.scheduled_end_at else None,
        "calling_windows": task.calling_windows_json,
        "excluded_dates": task.excluded_dates_json,
        "max_concurrent_calls": task.max_concurrent_calls,
        "calls_per_minute": task.calls_per_minute,
        "retry_policy": task.retry_policy_json,
        "callback_url": task.callback_url,
        "callback_secret_ref": task.callback_secret_ref,
        "state_version": task.state_version,
        "result_revision": task.result_revision,
        "last_error_code": task.last_error_code,
        "contact_statistics": status_counts,
        "created_at": isoformat(task.created_at),
        "started_at": isoformat(task.started_at) if task.started_at else None,
        "completed_at": isoformat(task.completed_at) if task.completed_at else None,
    }


def contact_view(contact, history):
    return {
        "contact_id": contact.id,
        "task_id": contact.task_id,
        "customer_contact_id": contact.customer_contact_id,
        "import_seq": contact.import_seq,
        "phone_last4": contact.phone_last4,
        "status": contact.status,
        "retry_count": contact.retry_count,
        "active_call_id": contact.active_call_id,
        "next_call_at": isoformat(contact.next_call_at) if contact.next_call_at else None,
        "result_code": contact.final_result_code,
        "last_error_code": contact.last_error_code,
        "result_revision": contact.result_revision,
        "state_version": contact.state_version,
        "call_count": history["call_count"],
        "last_call_id": history["last_call_id"],
        "created_at": isoformat(contact.created_at),
        "updated_at": isoformat(contact.updated_at),
        "finished_at": isoformat(contact.finished_at) if contact.finished_at else None,
    }


def call_view(call):
    result = {}
    for key, value in call.__dict__.items():
        if isinstance(value, datetime.datetime):
            value = isoformat(value)
        result[key] = value
    result["call_id"] = call.id
    return result
