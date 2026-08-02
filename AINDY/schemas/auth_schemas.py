from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    username: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    """FR-6 item 1 — self-service password rotation for an authenticated user."""
    current_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    """FR-6 item 2. Only an email — the response is identical either way."""
    email: str


class ResetPasswordRequest(BaseModel):
    """FR-6 item 3."""
    token: str
    new_password: str
