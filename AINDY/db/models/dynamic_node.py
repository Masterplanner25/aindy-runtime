"""
db/models/dynamic_node.py - Persisted dynamic node registrations.

Stores every node registered via POST /platform/nodes/register.
On startup the platform loader reads all active rows and rebuilds each
node function (webhook factory or plugin import) then registers it into
NODE_REGISTRY.

handler_config schema:
    webhook:  {"url": "https://...", "timeout_seconds": 10}
    plugin:   {"handler": "my_module:my_function"}

The signing secret for webhook nodes is stored in the separate `secret`
column (plaintext) because it is an outgoing signing credential. The
owner_class is persisted separately so restart restore paths keep the same
trust boundary that applied at registration time.

Deletion is soft - is_active=False.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSON, UUID

from AINDY.db.database import Base
from AINDY.platform_layer.extension_policy import OWNER_EXTERNAL_THIRD_PARTY


class DynamicNode(Base):
    __tablename__ = "dynamic_nodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(256), nullable=False, unique=True, index=True)
    node_type = Column(String(32), nullable=False)
    owner_class = Column(String(64), nullable=False, default=OWNER_EXTERNAL_THIRD_PARTY)
    handler_config = Column(JSON, nullable=False)
    provenance = Column(JSON, nullable=True)
    secret = Column(String(512), nullable=True)
    created_by = Column(String(256), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    is_active = Column(Boolean, nullable=False, default=True)
