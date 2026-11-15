import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from fastcall.application.outbound_queries import console_call_view, console_task_view
from fastcall.domain.contact_variables import validate_contact_variables
from fastcall_core.application.dto import new_id, utcnow
from fastcall_core.application.outbound_ports import OutboundTaskRecord, TaskContactRecord
from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.domain.outbound_tasks import TaskStatus
from fastcall_core.domain.retry_policy import RetryPolicy


MOCK_CALL_ORIGIN = "mock_call"
MOCK_CALL_PHONE = "+12025550100"


@dataclass
class MockCallTicket:
    task_id: str
    ticket: str
    actor: str
    expires_at: float
    claimed: bool = False


class MockCallTicketStore:
    def __init__(self, ttl_seconds=60):
        self.ttl_seconds = ttl_seconds
        self._items = {}
        self._lock = threading.Lock()

    def create(self, task_id, actor):
        with self._lock:
            now = time.time()
            self._items = {
                key: value for key, value in self._items.items() if value.expires_at > now
            }
            item = MockCallTicket(task_id, secrets.token_urlsafe(32), actor, now + self.ttl_seconds)
            self._items[task_id] = item
            return item

    def claim(self, task_id, ticket, actor):
        with self._lock:
            item = self._items.get(task_id)
            if (
                item is None or item.claimed or item.expires_at <= time.time()
                or item.actor != actor or not secrets.compare_digest(item.ticket, ticket)
            ):
                raise RobotConfigError(
                    "MOCK_CALL_SESSION_INVALID",
                    "Mock Call session is invalid, expired or already used",
                )
            item.claimed = True
            return item

    def remove(self, task_id):
        with self._lock:
            self._items.pop(task_id, None)


class PrepareMockCallUseCase:
    def __init__(
        self, uow, secret_codec, phone_hmac, *, callback_url, callback_secret_ref,
        clock=utcnow, enabled=True,
    ):
        self.uow = uow
        self.secret_codec = secret_codec
        self.phone_hmac = phone_hmac
        self.callback_url = callback_url
        self.callback_secret_ref = callback_secret_ref
        self.clock = clock
        self.enabled = enabled

    def prepare(self, app_id, robot_version_id, variables, client_request_id, actor):
        if not self.enabled:
            raise RobotConfigError("MOCK_CALL_DISABLED", "Mock Call is disabled")
        if not self.callback_url or not self.callback_secret_ref:
            raise RobotConfigError(
                "MOCK_CALL_NOT_CONFIGURED", "Mock Call receiver is not configured"
            )
        try:
            task = self.uow.tasks.get_by_customer_id(client_request_id)
            if task is not None:
                self._verify_existing(task, app_id, robot_version_id, variables)
                self.uow.rollback()
                return console_task_view(task)
            app = self.uow.apps.get_for_update(app_id)
            if app is None:
                raise RobotConfigError("ROBOT_APP_NOT_FOUND", "Robot application not found")
            if not app.enabled:
                raise RobotConfigError("ROBOT_DISABLED", "Robot application is disabled")
            release = self.uow.releases.get_for_update(app_id)
            if release is None or release.robot_app_version_id != robot_version_id:
                raise RobotConfigError(
                    "MOCK_CALL_RELEASE_CHANGED", "Robot release changed; reload and retry"
                )
            version = self.uow.versions.get(app_id, robot_version_id)
            if version is None:
                raise RobotConfigError("ROBOT_VERSION_NOT_FOUND", "Robot version not found")
            values = validate_contact_variables(version.content_json.get("variables"), variables)
            now = self.clock()
            task = OutboundTaskRecord(
                id=new_id("task"), customer_task_id=client_request_id,
                name=f"Mock Call · {app.name}", robot_app_id=app.id,
                robot_version_id=version.id, config_hash=version.config_hash,
                status=TaskStatus.DRAFT.value, health_status="healthy", timezone="UTC",
                default_country_code="1", scheduled_start_at=None, scheduled_end_at=None,
                calling_windows_json=[], excluded_dates_json=[], max_concurrent_calls=1,
                calls_per_minute=1, next_dial_at=None, last_dispatched_at=None,
                retry_policy_json=RetryPolicy(0, (), frozenset()).as_dict(),
                callback_url=self.callback_url, callback_secret_ref=self.callback_secret_ref,
                state_version=1, result_revision=0, last_error_code=None,
                created_at=now, started_at=None, completed_at=None, created_by=actor,
                origin=MOCK_CALL_ORIGIN,
            )
            self.uow.tasks.add(task)
            contact_id = new_id("contact")
            self.uow.contacts.add(TaskContactRecord(
                id=contact_id, task_id=task.id, customer_contact_id="mock-callee",
                import_seq=1,
                phone_ciphertext=self.secret_codec.encrypt(MOCK_CALL_PHONE),
                phone_last4=MOCK_CALL_PHONE[-4:],
                phone_hmac=self.phone_hmac.sign(MOCK_CALL_PHONE),
                hmac_key_version=self.phone_hmac.active_version,
                variables_ciphertext=self.secret_codec.encrypt_json(values),
                encryption_key_version=self.secret_codec.active_version,
                status="pending", retry_count=0, active_call_id=None,
                next_call_at=now, final_result_code=None, last_error_code=None,
                result_revision=0, state_version=1, created_at=now, updated_at=now,
                finished_at=None,
            ))
            self.uow.commit()
            return console_task_view(task)
        except Exception:
            self.uow.rollback()
            raise

    def _verify_existing(self, task, app_id, robot_version_id, variables):
        if (
            task.origin != MOCK_CALL_ORIGIN or task.robot_app_id != app_id
            or task.robot_version_id != robot_version_id
        ):
            raise RobotConfigError(
                "OUTBOUND_TASK_EXISTS", "client_request_id has different content"
            )
        contacts = self.uow.queries.all_contacts(task.id)
        if len(contacts) != 1:
            raise RobotConfigError("OUTBOUND_TASK_EXISTS", "Mock Call task is invalid")
        version = self.uow.versions.get(task.robot_app_id, task.robot_version_id)
        values = validate_contact_variables(version.content_json.get("variables"), variables)
        old_variables = self.secret_codec.decrypt_json(
            contacts[0].variables_ciphertext, contacts[0].encryption_key_version
        )
        if old_variables != values:
            raise RobotConfigError(
                "OUTBOUND_TASK_EXISTS", "client_request_id has different content"
            )

    def arm(self, task_id, actor):
        if not self.enabled:
            raise RobotConfigError("MOCK_CALL_DISABLED", "Mock Call is disabled")
        try:
            task = self.uow.tasks.get_for_update(task_id)
            if task is None or task.origin != MOCK_CALL_ORIGIN or task.created_by != actor:
                raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Mock Call not found")
            if task.status == TaskStatus.RUNNING.value:
                self.uow.rollback()
                return console_task_view(task)
            if task.status != TaskStatus.DRAFT.value:
                raise RobotConfigError(
                    "MOCK_CALL_NOT_ARMABLE", "Only a prepared Mock Call can be activated"
                )
            task = self.uow.tasks.transition_status(
                task_id, TaskStatus.SCHEDULED.value, task.state_version, self.clock()
            )
            task = self.uow.tasks.transition_status(
                task_id, TaskStatus.RUNNING.value, task.state_version, self.clock()
            )
            self.uow.commit()
            return console_task_view(task)
        except Exception:
            self.uow.rollback()
            raise

    def get(self, task_id):
        task = self.uow.tasks.get(task_id)
        if task is None or task.origin != MOCK_CALL_ORIGIN:
            raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Mock Call not found")
        calls = self.uow.queries.list_calls(task_id, limit=2)
        deliveries = self.uow.queries.list_webhook_deliveries(task_id)
        return {
            "task": console_task_view(task),
            "call": console_call_view(calls[0]) if calls else None,
            "deliveries": [
                {
                    "delivery_id": item.id,
                    "event_type": item.event_type,
                    "status": item.status,
                    "attempt_count": item.attempt_count,
                    "last_http_status": item.last_http_status,
                    "delivered_at": item.delivered_at,
                }
                for item in deliveries
            ],
        }
