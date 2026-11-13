import datetime

from sqlalchemy import or_, select

from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.infrastructure.outbound_orm import WebhookDeliveryModel


class SqlAlchemyWebhookWorkerRepository:
    def __init__(self, session):
        self.session = session

    def claim_due(self, now, owner, lease_seconds=30, limit=20):
        lease_end = now + datetime.timedelta(seconds=lease_seconds)
        models = self.session.execute(
            select(WebhookDeliveryModel)
            .where(
                or_(
                    WebhookDeliveryModel.status == "pending",
                    (WebhookDeliveryModel.status == "delivering")
                    & (WebhookDeliveryModel.delivery_lease_until <= now),
                ),
                or_(
                    WebhookDeliveryModel.next_delivery_at.is_(None),
                    WebhookDeliveryModel.next_delivery_at <= now,
                ),
            )
            .order_by(WebhookDeliveryModel.created_at, WebhookDeliveryModel.id)
            .limit(limit)
            .with_for_update()
        ).scalars().all()
        for model in models:
            model.status = "delivering"
            model.delivery_owner = owner
            model.delivery_lease_until = lease_end
            model.attempt_count += 1
        self.session.flush()
        return models

    def mark_delivered(self, delivery_id, owner, http_status, now):
        model = self.session.get(WebhookDeliveryModel, delivery_id)
        if model is None or model.delivery_owner != owner or model.status != "delivering":
            raise RobotConfigError("WEBHOOK_LEASE_LOST", "Webhook delivery lease is no longer owned")
        model.status = "delivered"
        model.last_http_status = http_status
        model.last_error = None
        model.delivered_at = now
        model.delivery_owner = None
        model.delivery_lease_until = None
        self.session.flush()

    def mark_retry(self, delivery_id, owner, error, now, *, max_attempts=10, http_status=None):
        model = self.session.get(WebhookDeliveryModel, delivery_id)
        if model is None or model.delivery_owner != owner or model.status != "delivering":
            raise RobotConfigError("WEBHOOK_LEASE_LOST", "Webhook delivery lease is no longer owned")
        model.last_http_status = http_status
        model.last_error = error[:1024]
        model.delivery_owner = None
        model.delivery_lease_until = None
        if model.attempt_count >= max_attempts:
            model.status = "failed"
            model.next_delivery_at = None
        else:
            model.status = "pending"
            delay = min(300, 5 * (2 ** (model.attempt_count - 1)))
            model.next_delivery_at = now + datetime.timedelta(seconds=delay)
        self.session.flush()
        return model.status

    def reset_failed(self, delivery_id, now):
        model = self.session.get(WebhookDeliveryModel, delivery_id)
        if model is None:
            raise RobotConfigError("WEBHOOK_DELIVERY_NOT_FOUND", "Webhook delivery not found")
        if model.status != "failed":
            raise RobotConfigError("WEBHOOK_NOT_FAILED", "Only failed deliveries can be reset")
        model.status = "pending"
        model.attempt_count = 0
        model.next_delivery_at = now
        model.last_error = None
        self.session.flush()
