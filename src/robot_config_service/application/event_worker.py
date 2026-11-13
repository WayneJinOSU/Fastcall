import asyncio
import uuid

from robot_config_service.application.dto import utcnow


class EventWorker:
    def __init__(self, uow_factory, handler, clock=utcnow, owner=None, max_attempts=10):
        self.uow_factory = uow_factory
        self.handler = handler
        self.clock = clock
        self.owner = owner or uuid.uuid4().hex
        self.max_attempts = max_attempts

    async def run_once(self):
        uow = self.uow_factory()
        try:
            events = uow.event_worker.claim_due(self.clock(), self.owner)
            uow.commit()
        finally:
            uow.close()
        results = []
        for event in events:
            outcome = self.uow_factory()
            try:
                await self.handler(outcome, event)
                outcome.event_worker.mark_processed(event.id, self.owner, self.clock())
                outcome.commit()
                results.append((event.id, "processed"))
            except Exception as error:
                outcome.rollback()
                outcome.event_worker.mark_retry(
                    event.id, self.owner, type(error).__name__, self.clock(), self.max_attempts
                )
                outcome.commit()
                results.append((event.id, "retrying"))
            finally:
                outcome.close()
        return results
