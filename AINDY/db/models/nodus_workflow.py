"""
db/models/nodus_workflow.py - Persisted Nodus workflow registrations (RTR-1).

Stores the ``.nd`` SOURCE of every workflow registered via
``register_nodus_workflow`` (imperative bootstrap call or the ``nodus-workflow``
declarative manifest kind). On server startup the platform loader reads all
active rows and re-validates each one so the runtime survives restarts.

Why source, not a compiled artifact
-----------------------------------
The durable, versioned artifact is the ``.nd`` source. It is deterministically
re-parsed/validated on registration and on every boot — nothing closure-bearing
or non-serialisable is persisted. See ``docs/runtime/NODUS_WORKFLOW_CONTRACT.md``.

kind (RTR-1a):
    "flow-graph" — a native Nodus ``workflow {}`` / ``goal {}`` program (steps
                   with logic, ``after`` dependencies, native orchestration).
                   Validated + its step DAG extracted via ``compile_nodus_flow``;
                   executed natively (``run_workflow``/``run_goal``).
    "script"     — a single arbitrary ``.nodus`` program run via the shared
                   ``nodus_execute`` flow.

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
