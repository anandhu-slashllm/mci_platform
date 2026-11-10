from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class TokenResponse(BaseModel):
    """A Supabase Auth session plus the app user it maps to."""

    access_token: str
    refresh_token: str
    expires_at: int = Field(description="Unix seconds when access_token expires")
    expires_in: int
    token_type: str = "bearer"
    user_id: int
    role: str
    locale: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1, max_length=2048)


class OtpRequestResponse(BaseModel):
    ok: bool = True
    message: str = "Verification code sent"
    dev_code: str | None = None
    delivery: str = Field(description="dev = code returned for local testing; email = sent by Supabase Auth")


class WorkerEmailOtpRequest(BaseModel):
    email: EmailStr
    locale: str | None = Field(default=None, description="en|zh — stored on new accounts")
    login_only: bool = Field(default=False, description="If true, reject unknown emails instead of allowing signup")


class WorkerEmailOtpVerify(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=10)
    locale: str | None = Field(default=None, description="en|zh — used when creating a new account")
    login_only: bool = Field(default=False, description="If true, reject unknown emails instead of creating an account")


class ClientEmailOtpRequest(BaseModel):
    email: EmailStr
    locale: str | None = Field(default=None, description="en|zh — stored on new accounts")
    login_only: bool = Field(default=False, description="If true, reject unknown emails instead of allowing signup")


class ClientEmailOtpVerify(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=10)
    locale: str | None = Field(default=None, description="en|zh — used when creating a new account")
    login_only: bool = Field(default=False, description="If true, reject unknown emails instead of creating an account")
