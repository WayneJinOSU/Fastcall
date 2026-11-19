import os
from dataclasses import dataclass


SUPPORTED_ENVIRONMENTS = {"dev", "test", "prod"}


def _env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    management_token: str
    resolve_token: str
    console_admin_actor_id: str = ""
    console_admin_password_hash: str = ""
    console_session_secret: str = ""
    collection_agent_ws_url: str = "ws://127.0.0.1:8765/ws-v2"
    collection_agent_http_url: str = "http://127.0.0.1:8765"
    adapter_internal_url: str = "http://127.0.0.1:8767"
    openapi_token: str = ""
    admin_token: str = ""
    adapter_token: str = ""
    agent_token: str = ""
    media_token: str = ""
    sip_enabled: bool = True

    @classmethod
    def from_env(cls):
        return cls(
            app_env=os.getenv("APP_ENV", "").strip(),
            database_url=(
                os.getenv("FASTCALL_DATABASE_URL", "").strip()
                or os.getenv("DATABASE_URL", "").strip()
            ),
            management_token=os.getenv("FASTCALL_MANAGEMENT_TOKEN", "").strip(),
            resolve_token=os.getenv("FASTCALL_RESOLVE_TOKEN", "").strip(),
            console_admin_actor_id=os.getenv("FASTCALL_CONSOLE_ADMIN_ACTOR_ID", "").strip(),
            console_admin_password_hash=os.getenv("FASTCALL_CONSOLE_ADMIN_PASSWORD_HASH", "").strip(),
            console_session_secret=os.getenv("FASTCALL_CONSOLE_SESSION_SECRET", "").strip(),
            collection_agent_ws_url=(
                os.getenv("COLLECTION_AGENT_WS_URL", "ws://127.0.0.1:8765/ws-v2").strip()
            ),
            collection_agent_http_url=(
                os.getenv("COLLECTION_AGENT_HTTP_URL", "http://127.0.0.1:8765").strip()
            ),
            adapter_internal_url=(
                os.getenv("FASTCALL_ADAPTER_INTERNAL_URL", "http://127.0.0.1:8767").strip()
            ),
            openapi_token=os.getenv("FASTCALL_OPENAPI_TOKEN", "").strip(),
            admin_token=os.getenv("FASTCALL_ADMIN_TOKEN", "").strip(),
            adapter_token=os.getenv("FASTCALL_ADAPTER_TOKEN", "").strip(),
            agent_token=os.getenv("FASTCALL_AGENT_TOKEN", "").strip(),
            media_token=os.getenv("FASTCALL_MEDIA_TOKEN", "").strip(),
            sip_enabled=_env_bool("FASTCALL_SIP_ENABLED", True),
        )

    def validate(self):
        if self.app_env not in SUPPORTED_ENVIRONMENTS:
            raise RuntimeError("APP_ENV must be one of: dev, test, prod")
        if not self.database_url.startswith("mysql+pymysql://"):
            raise RuntimeError("Fast-Call requires a MySQL PyMySQL database URL")
        tokens = [
            self.management_token, self.resolve_token, self.openapi_token,
            self.admin_token, self.adapter_token, self.agent_token, self.media_token,
        ]
        if any(not token for token in tokens):
            raise RuntimeError("All Fast-Call API tokens are required")
        if len(set(tokens)) != len(tokens):
            raise RuntimeError("All Fast-Call API tokens must be different")
        if not self.console_admin_actor_id or not self.console_admin_password_hash:
            raise RuntimeError("Console administrator identity and password hash are required")
        if len(self.console_session_secret) < 32:
            raise RuntimeError("Console session secret must be at least 32 characters")
        if not self.sip_enabled:
            raise RuntimeError("SIP must be enabled")
