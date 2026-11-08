"""
db/models/user_provider_key.py — a user's own provider key, encrypted at rest (FR-59, DEC-121).

One row per (user, provider). The key is stored only as AES-GCM ciphertext, sealed with a
master key the runtime never derives from SECRET_KEY; ``key_hint`` (the last four
characters) is the only part that can be shown. Design:
docs/design/PROVIDER_KEYS_PER_USER_DESIGN.md.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from AINDY.db.database import Base


class UserProviderKey(Base):
    __tablename__ = "user_provider_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_provider_keys_user_provider"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(32), nullable=False)
    ciphertext = Column(Text, nullable=False)
    key_hint = Column(String(8), nullable=False)
    key_version = Column(String(16), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
