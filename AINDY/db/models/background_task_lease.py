from sqlalchemy import BigInteger, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from AINDY.db.database import Base


class BackgroundTaskLease(Base):
    __tablename__ = "background_task_leases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True, index=True)
    owner_id = Column(String, nullable=False, index=True)
    acquired_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    heartbeat_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # LEASE-FENCE-1 — monotonic, incremented ONLY on takeover (1 on first claim, unchanged on
    # renew). A leader-only job reads it FOR SHARE inside its own transaction and refuses to
    # commit if it moved: a stale leader is refused, not asked to notice. Alembic 0019.
    fence = Column(BigInteger, nullable=False, server_default="0", default=0)
