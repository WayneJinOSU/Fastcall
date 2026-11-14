from fastcall_core.application.dto import utcnow


class ResetWebhookDeliveryUseCase:
    def __init__(self, uow, clock=utcnow):
        self.uow = uow
        self.clock = clock

    def execute(self, delivery_id):
        self.uow.webhook_worker.reset_failed(delivery_id, self.clock())
        self.uow.commit()
        return {"delivery_id": delivery_id, "status": "pending"}
