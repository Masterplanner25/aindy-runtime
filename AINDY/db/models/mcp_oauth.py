"""
db/models/mcp_oauth.py — OAuth for the MCP endpoint (FR-66 phase 2, DEC-129).

Two tables, read and written only by ``AINDY/platform_layer/mcp_oauth.py``:

* ``mcp_oauth_clients`` — dynamic client registrations (RFC 7591). The client's metadata, and
  its client secret only as ciphertext.
* ``mcp_oauth_grants`` — one row per consent: which user let which client do what. It holds the
  pending authorization code and the current refresh token as SHA-256 hashes only, never the
  value. Revoking a grant ends every token issued under it.

Nothing here is read unless ``AINDY_MCP_OAUTH`` is on. Design:
docs/design/MCP_REMOTE_TRANSPORT_DESIGN.md §5–§6.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from AINDY.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class McpOAuthClient(Base):
    __tablename__ = "mcp_oauth_clients"

    client_id = Column(String(64), primary_key=True)
    # OAuthClientInformationFull as JSON, without the client secret.
    client_info = Column(Text, nullable=False)
    # The client secret, sealed (MultiFernet over keys derived from the signing keys); null for a
    # public client (token_endpoint_auth_method "none").
    client_secret_ciphertext = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class McpOAuthGrant(Base):
    __tablename__ = "mcp_oauth_grants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id = Column(
        String(64),
        ForeignKey("mcp_oauth_clients.client_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Space-separated, as OAuth writes them (portable across Postgres and SQLite).
    scopes = Column(Text, nullable=False, default="")
    resource = Column(Text, nullable=True)
    # The user's token_version at consent; a password change moves it and ends the grant.
    token_version = Column(Integer, nullable=False, default=0)

    # The pending authorization code: single-use, 60 seconds.
    code_hash = Column(String(64), nullable=True, unique=True)
    code_challenge = Column(String(128), nullable=True)
    code_redirect_uri = Column(Text, nullable=True)
    code_redirect_explicit = Column(Boolean, nullable=False, default=True)
    code_expires_at = Column(DateTime(timezone=True), nullable=True)

    # The current refresh token (rotates on use) and the one it replaced, so a replay of the
    # old one is recognised and revokes the grant.
    refresh_token_hash = Column(String(64), nullable=True, unique=True)
    previous_refresh_token_hash = Column(String(64), nullable=True, index=True)
    refresh_expires_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
