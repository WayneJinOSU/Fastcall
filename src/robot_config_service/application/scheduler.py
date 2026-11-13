import asyncio
import datetime
import logging

from robot_config_service.application.dto import new_id, utcnow


logger = logging.getLogger(__name__)


class DialScheduler:
    def __init__(
        self, uow_factory, dispatcher, secret_codec, phone_hmac, *,
        global_max_concurrent=50, global_calls_per_minute=60,
        reservation_timeout_sec=30, scan_interval_sec=0.5, clock=utcnow,
    ):
        self.uow_factory = uow_factory
        self.dispatcher = dispatcher
        self.secret_codec = secret_codec
        self.phone_hmac = phone_hmac
        self.global_max_concurrent = global_max_concurrent
        self.global_calls_per_minute = global_calls_per_minute
        self.reservation_timeout_sec = reservation_timeout_sec
        self.scan_interval_sec = scan_interval_sec
        self.clock = clock

    def _hmac_versions(self, uow, contact):
        phone = self.secret_codec.decrypt(
            contact.phone_ciphertext, contact.encryption_key_version
        )
        return self.phone_hmac.all_versions(phone)

    def recover_expired_reservations(self):
        now = self.clock()
        uow = self.uow_factory()
        try:
            recovered = []
            for call in uow.scheduling.list_expired_reservations(now):
                uow.calls.release_unauthorized_reservation(call.id, call.state_version, now)
                recovered.append(call.id)
            uow.commit()
            return recovered
        except Exception:
            uow.rollback()
            raise
        finally:
            uow.close()

    async def scan_once(self):
        self.recover_expired_reservations()
        now = self.clock()
        created = []
        task_uow = self.uow_factory()
        try:
            task_uow.scheduling.promote_scheduled_tasks(now)
            task_uow.commit()
            tasks = task_uow.scheduling.list_running_tasks()
        finally:
            task_uow.close()
        for task in tasks:
            uow = self.uow_factory()
            try:
                uow.scheduling.promote_due_retries(task.id, now)
                contacts = uow.contacts.list_due(task.id, now)
                if not contacts:
                    uow.commit()
                    continue
                contact = contacts[0]
                call_id = new_id("call")
                call = uow.scheduling.reserve_dialable(
                    contact.id,
                    call_id,
                    new_id("origin"),
                    now,
                    now + datetime.timedelta(seconds=self.reservation_timeout_sec),
                    self._hmac_versions(uow, contact),
                    self.global_max_concurrent,
                    self.global_calls_per_minute,
                )
                if call is None:
                    uow.rollback()
                    continue
                uow.commit()
                created.append(call_id)
            except Exception:
                uow.rollback()
                raise
            finally:
                uow.close()
            try:
                await self.dispatcher.dispatch(call_id)
            except Exception as error:
                logger.warning("Dispatch rejected for call_id=%s: %s", call_id, type(error).__name__)
        return created

    async def run(self, stop_event=None):
        stop_event = stop_event or asyncio.Event()
        while not stop_event.is_set():
            await self.scan_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.scan_interval_sec)
            except asyncio.TimeoutError:
                pass
