import datetime
from urllib.parse import urlsplit

from robot_config_service.application.call_results import CompleteTaskUseCase
from robot_config_service.application.dto import isoformat, new_id, utcnow
from robot_config_service.application.outbound_ports import OutboundTaskRecord, TaskContactRecord
from robot_config_service.application.resolve import ResolveUseCase
from robot_config_service.domain.calling_windows import validate_calling_windows
from robot_config_service.domain.contact_variables import validate_contact_variables
from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.domain.outbound_tasks import TaskStatus
from robot_config_service.domain.phone_numbers import normalize_e164
from robot_config_service.domain.retry_policy import RetryPolicy


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise RobotConfigError("INVALID_TASK_SCHEDULE", "Task schedule must include an offset")
    return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _validate_callback(url):
    if url is None:
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RobotConfigError("INVALID_CALLBACK_URL", "Callback URL must be HTTPS without user info")
    return url


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


class OutboundTasksUseCase:
    def __init__(self, uow, secret_codec, phone_hmac, clock=utcnow):
        self.uow = uow
        self.secret_codec = secret_codec
        self.phone_hmac = phone_hmac
        self.clock = clock

    def create(self, customer_task_id, data, created_by="openapi"):
        now = self.clock()
        app = self.uow.apps.get_by_code(data["robot_code"])
        if app is None:
            raise RobotConfigError("ROBOT_APP_NOT_FOUND", "Robot application not found")
        start = _utc(data.get("scheduled_start_at"))
        end = _utc(data.get("scheduled_end_at"))
        if start and end and start >= end:
            raise RobotConfigError("INVALID_TASK_SCHEDULE", "Task start must be before its end")
        windows = data.get("calling_windows", [])
        excluded = data.get("excluded_dates", [])
        validate_calling_windows(windows, excluded, data["timezone"])
        retry = RetryPolicy.from_dict(data["retry_policy"]).as_dict()
        callback = _validate_callback(data.get("callback_url"))
        values = {
            "customer_task_id": customer_task_id,
            "name": data["name"],
            "robot_app_id": app.id,
            "timezone": data["timezone"],
            "default_country_code": data["default_country_code"],
            "scheduled_start_at": start,
            "scheduled_end_at": end,
            "calling_windows_json": windows,
            "excluded_dates_json": excluded,
            "max_concurrent_calls": data["max_concurrent_calls"],
            "calls_per_minute": data["calls_per_minute"],
            "retry_policy_json": retry,
            "callback_url": callback,
            "callback_secret_ref": data.get("callback_secret_ref"),
        }
        existing = self.uow.tasks.get_by_customer_id(customer_task_id)
        if existing:
            if all(getattr(existing, key) == value for key, value in values.items()):
                return task_view(existing)
            raise RobotConfigError("OUTBOUND_TASK_EXISTS", "customer_task_id has different content")
        task = OutboundTaskRecord(
            id=new_id("task"), robot_version_id=None, config_hash=None,
            status=TaskStatus.DRAFT.value, health_status="healthy",
            next_dial_at=None, last_dispatched_at=None, state_version=1,
            result_revision=0, last_error_code=None, created_at=now,
            started_at=None, completed_at=None, created_by=created_by,
            **values,
        )
        self.uow.tasks.add(task)
        self.uow.commit()
        return task_view(task)

    def get(self, task_id):
        task = self.uow.tasks.get(task_id)
        if task is None:
            raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Outbound task not found")
        return task_view(task, self.uow.results.contact_status_counts(task_id))

    def import_contacts(self, task_id, items):
        task = self.uow.tasks.get_for_update(task_id)
        if task is None:
            raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Outbound task not found")
        if task.status not in (TaskStatus.DRAFT.value, TaskStatus.SCHEDULED.value):
            raise RobotConfigError("CONTACTS_IMMUTABLE", "Contacts cannot change after task start")
        app = self.uow.apps.get(task.robot_app_id)
        accepted = []
        duplicates = []
        batch_phones = set()
        next_seq = self.uow.queries.next_import_seq(task_id)
        now = self.clock()
        for item in items:
            phone = normalize_e164(item["phone"], task.default_country_code)
            variables = validate_contact_variables(app.draft_json.get("variables"), item.get("variables"))
            hmacs = self.phone_hmac.all_versions(phone)
            active_hmac = self.phone_hmac.sign(phone)
            existing = self.uow.contacts.get_by_customer_id(task_id, item["customer_contact_id"])
            if existing:
                old_phone = self.secret_codec.decrypt(
                    existing.phone_ciphertext, existing.encryption_key_version
                )
                old_variables = self.secret_codec.decrypt_json(
                    existing.variables_ciphertext, existing.encryption_key_version
                )
                if old_phone != phone or old_variables != variables:
                    raise RobotConfigError(
                        "CONTACT_ID_CONFLICT", "customer_contact_id has different content"
                    )
                continue
            existing = self.uow.contacts.find_by_phone_hmacs(task_id, [value[0] for value in hmacs])
            if existing or active_hmac in batch_phones:
                duplicates.append({
                    "customer_contact_id": item["customer_contact_id"],
                    "phone_last4": phone[-4:],
                    "error_code": "DUPLICATE_PHONE",
                })
                continue
            contact = TaskContactRecord(
                id=new_id("contact"), task_id=task_id,
                customer_contact_id=item["customer_contact_id"], import_seq=next_seq,
                phone_ciphertext=self.secret_codec.encrypt(phone), phone_last4=phone[-4:],
                phone_hmac=active_hmac, hmac_key_version=self.phone_hmac.active_version,
                variables_ciphertext=self.secret_codec.encrypt_json(variables),
                encryption_key_version=self.secret_codec.active_version,
                status="pending", retry_count=0, active_call_id=None, next_call_at=None,
                final_result_code=None, last_error_code=None, result_revision=0,
                state_version=1, created_at=now, updated_at=now, finished_at=None,
            )
            self.uow.contacts.add(contact)
            batch_phones.add(active_hmac)
            accepted.append(contact_view(contact, {"call_count": 0, "last_call_id": None}))
            next_seq += 1
        self.uow.commit()
        return {"accepted_contacts": accepted, "duplicate_contacts": duplicates}

    def start(self, task_id, expected_state_version):
        task = self.uow.tasks.get_for_update(task_id)
        if task is None:
            raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Outbound task not found")
        if self.uow.queries.next_import_seq(task_id) == 1:
            raise RobotConfigError("CONTACTS_REQUIRED", "At least one contact is required")
        app = self.uow.apps.get(task.robot_app_id)
        resolved = ResolveUseCase(self.uow).execute(app.app_code)
        for contact in self.uow.queries.all_contacts(task_id):
            variables = self.secret_codec.decrypt_json(
                contact.variables_ciphertext, contact.encryption_key_version
            )
            validate_contact_variables(resolved["app_config"].get("variables"), variables)
        task = self.uow.tasks.pin_robot(
            task_id, resolved["robot_app_version_id"], resolved["config_hash"], expected_state_version
        )
        now = self.clock()
        if task.status == TaskStatus.DRAFT.value:
            task = self.uow.tasks.transition_status(
                task_id, TaskStatus.SCHEDULED.value, task.state_version, now
            )
        if task.scheduled_start_at and task.scheduled_start_at > now:
            self.uow.commit()
            return task_view(task)
        task = self.uow.tasks.transition_status(
            task_id, TaskStatus.RUNNING.value, task.state_version, now
        )
        self.uow.commit()
        return task_view(task)

    def pause(self, task_id, expected_state_version):
        task = self.uow.tasks.transition_status(
            task_id, TaskStatus.PAUSED.value, expected_state_version, self.clock()
        )
        self.uow.commit()
        return task_view(task)

    def resume(self, task_id, expected_state_version):
        now = self.clock()
        task = self.uow.tasks.transition_status(
            task_id, TaskStatus.RUNNING.value, expected_state_version, now
        )
        completed = CompleteTaskUseCase(self.uow, self.clock).execute(
            task_id, now=now, commit=False
        )
        if completed is not None:
            task = completed
        self.uow.commit()
        return task_view(task)
