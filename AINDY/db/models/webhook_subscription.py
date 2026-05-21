"""
db/models/webhook_subscription.py - Persisted webhook subscriptions.

Stores every subscription created via POST /platform/webhooks.
On startup the platform loader reads all active rows and re-loads them
into the in-memory _SUBSCRIPTIONS dict in event_service.py, restoring
the same subscription IDs so any client that stored a subscription_id
continues to work after a restart.

The `id` column doubles as the subscription_id returned to callers. The
owner_class is persisted so the runtime can report whether the subscription
belongs to runtime, first-party, or external ownership after restart.

Deletion is soft - is_active=False.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSON, UUID

from AINDY.db.database import Base
from AINDY.platform_layer.extension_policy import OWNER_EXTERNAL_THIRD_PARTY


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True)
    event_type = Column(String(256), nullable=False, index=True)
    callback_url = Column(String(2048), nullable=False)
    owner_class = Column(String(64), nullable=False, default=OWNER_EXTERNAL_THIRD_PARTY)
    provenance = Column(JSON, nullable=True)
    secret = Column(String(512), nullable=True)
    created_by = Column(String(256), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    is_active = Column(Boolean, nullable=False, default=True)
