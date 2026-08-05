"""
db/models/user.py — Persisted User model for A.I.N.D.Y. authentication.

Phase 3 replacement for the in-memory _USERS dict in auth_router.py.
"""
import uuid
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from AINDY.db.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=True)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    token_version = Column(Integer, default=0, nullable=False, server_default="0")
    # FR-6 Phase C. New registrations start unverified (Python-side default False) and are
    # verified by following an emailed link. Note the deliberate asymmetry with the
    # migration, which backfills EXISTING rows to true: those accounts predate verification
    # and were never given a chance to confirm, so grandfathering them is the only option
    # that does not retroactively lock out every current user.
    # `reconcile_backfill` (FR-8) is the machine-readable half of the comment above. The
    # Alembic 0014 grandfathering never runs on a wheel install -- the alembic/ tree is not
    # packaged -- so `bootstrap-schema --reconcile` applies server_default and nothing else,
    # and every pre-existing account comes back unverified. Declaring it here makes the
    # guarantee hold on every install shape. Applied only when the column is first added to
    # a table that already has rows; see schema_contract._render_backfill_sql.
    is_verified = Column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",
        info={"reconcile_backfill": "true"},
    )
    verified_at = Column(
        DateTime(timezone=True),
        nullable=True,
        # COALESCE mirrors 0014: created_at is nullable, and a grandfathered row still needs
        # a timestamp.
        info={"reconcile_backfill": "COALESCE(created_at, now())"},
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    api_keys = relationship("PlatformAPIKey", back_populates="user", cascade="all, delete-orphan")
