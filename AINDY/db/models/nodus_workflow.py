"""
db/models/nodus_workflow.py - Persisted Nodus workflow registrations (RTR-1).

Stores the ``.nd`` SOURCE of every workflow registered via
``register_nodus_workflow`` (imperative bootstrap call or the ``nodus-workflow``
declarative manifest kind). On server startup the platform loader reads all
active rows and recompiles each one into ``FLOW_REGISTRY`` so the runtime
survives restarts.

Why source, not the compiled flow dict
--------------------------------------
``compile_nodus_flow`` produces flow dicts whose conditional edges are in-memory
Python closures that are not serialisable. The durable, versioned artifact is the
``.nd`` source; the compiled flow is ephemeral and is deterministically
reconstructed from source on registration and on every boot. See
``docs/runtime/NODUS_WORKFLOW_CONTRACT.md``.

kind:
    "flow-graph" — a ``flow.step()`` routing script compiled via
                   ``compile_nodus_flow`` into a multi-node flow over registered
                   nodes.
    "script"     — a single arbitrary ``.nodus`` program run as one
                   ``nodus.execute`` node.

Deletion is soft - is_active=False - so the audit trail is preserved.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID

from AINDY.db.database import Base
from AINDY.platform_layer.extension_policy import OWNER_EXTERNAL_THIRD_PARTY


class NodusWorkflow(Base):
    __tablename__ = "nodus_workflows"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(256), nullable=False)
    source = Column(Text, nullable=False)
    kind = Column(String(16), nullable=False, default="flow-graph")
    version = Column(String(128), nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    capabilities = Column(JSON, nullable=True)
    owner_class = Column(String(64), nullable=False, default=OWNER_EXTERNAL_THIRD_PARTY)
    provenance = Column(JSON, nullable=True)
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

    __table_args__ = (
        Index("uq_nodus_workflows_name", "name", unique=True),
    )
