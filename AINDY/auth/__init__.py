"""auth package — canonical implementation lives in api_key_auth.py."""
from AINDY.auth.api_key_auth import (  # noqa: F401
    AuthPrincipal,
    Scopes,
    get_authenticated_principal,
    require_scope,
)
