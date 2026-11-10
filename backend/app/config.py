from functools import lru_cache
from typing import Any, List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_bool(v: Any, *, default: bool) -> bool:
    """Parse bool from env strings (.env often uses false, "false", quoted values)."""
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return default
    s = str(v).strip().strip('"').strip("'").lower()
    if s in ("0", "false", "no", "off", "n", "disabled"):
        return False
    if s in ("1", "true", "yes", "on", "y", "enabled"):
        return True
    return default


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Supabase Postgres. Local CLI stack: postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres
    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"
    # "pooled" = long-running server with a SQLAlchemy pool (Docker, uvicorn).
    # "serverless" = no client-side pool + pgbouncer/Supavisor-safe asyncpg settings (Vercel functions).
    db_pool_mode: str = "pooled"
    # Signs short-lived file-download and attendance-QR tokens (NOT user sessions; Supabase Auth issues those).
    secret_key: str = "dev-secret-change-in-production"

    # --- Supabase -------------------------------------------------------------------------------
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_anon_key: str = ""  # a.k.a. publishable key
    supabase_service_role_key: str = ""  # a.k.a. secret key; server-side only
    # Legacy symmetric JWT secret (HS256). Hosted projects using asymmetric signing keys verify via JWKS instead.
    supabase_jwt_secret: str = ""
    supabase_storage_bucket: str = "mci-uploads"
    # Browser-facing Supabase URL for signed download links, when the API reaches Supabase on an
    # internal address (e.g. host.docker.internal from the Docker image). Defaults to SUPABASE_URL.
    supabase_public_url: str = ""
    # Local dev only: Mailpit/Inbucket base URL. When set and OTP_DEV_MODE=true, the OTP request
    # endpoints read the code back from the dev mailbox and return it as `dev_code`.
    supabase_dev_mailbox_url: str = ""

    # Background work. "loop" = in-process asyncio loops (long-running server).
    # "cron" = only via /internal/cron/* endpoints (Vercel Cron / pg_cron), authenticated with CRON_SECRET.
    background_mode: str = "loop"
    cron_secret: str = ""
    fernet_key: str = ""  # optional; if empty, PII stored plaintext in dev only (not recommended prod)

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    otp_dev_mode: bool = True
    shift_attendance_change_cutoff_hours: int = 4

    max_upload_bytes: int = 15 * 1024 * 1024  # override via MAX_UPLOAD_BYTES

    support_email: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""


    @field_validator("otp_dev_mode", mode="before")
    @classmethod
    def coerce_otp_dev_mode(cls, v: Any) -> bool:
        return _env_bool(v, default=True)

    @field_validator(
        "email_from",
        "supabase_url",
        "supabase_anon_key",
        "supabase_service_role_key",
        "supabase_jwt_secret",
        "cron_secret",
        mode="before",
    )
    @classmethod
    def strip_quotes_whitespace(cls, v: Any) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
            s = s[1:-1].strip()
        return s

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
