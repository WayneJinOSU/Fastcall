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
    collection_agent_ws_url: str = "ws://127.0.0.1:8765/ws-v2"
    collection_agent_http_url: str = "http://127.0.0.1:8765"
    admin_token: str = ""
    adapter_token: str = ""
    agent_token: str = ""
    media_token: str = ""
    freeswitch_sip_gateway: str = ""
    sip_enabled: bool = True
    mock_calls_enabled: bool = True
    sip_register: bool = True

    @classmethod
    def from_env(cls):
        return cls(
            app_env=os.getenv("APP_ENV", "").strip(),
            database_url=(
                os.getenv("FASTCALL_DATABASE_URL", "").strip()
                or os.getenv("DATABASE_URL", "").strip()
            ),
            collection_agent_ws_url=(
                os.getenv("COLLECTION_AGENT_WS_URL", "ws://127.0.0.1:8765/ws-v2").strip()
            ),
            collection_agent_http_url=(
                os.getenv("COLLECTION_AGENT_HTTP_URL", "http://127.0.0.1:8765").strip()
            ),
            admin_token=os.getenv("FASTCALL_ADMIN_TOKEN", "").strip(),
            adapter_token=os.getenv("FASTCALL_ADAPTER_TOKEN", "").strip(),
            agent_token=os.getenv("FASTCALL_AGENT_TOKEN", "").strip(),
            media_token=os.getenv("FASTCALL_MEDIA_TOKEN", "").strip(),
            freeswitch_sip_gateway=os.getenv("FREESWITCH_SIP_GATEWAY", "").strip(),
            sip_enabled=_env_bool("FASTCALL_SIP_ENABLED", True),
            mock_calls_enabled=_env_bool("FASTCALL_MOCK_CALLS_ENABLED", True),
            sip_register=_env_bool("FREESWITCH_SIP_REGISTER", True),
        )

    def validate(self):
        if self.app_env not in SUPPORTED_ENVIRONMENTS:
            raise RuntimeError("APP_ENV must be one of: dev, test, prod")
        if not self.database_url.startswith("mysql+pymysql://"):
            raise RuntimeError("Fast-Call Adapter requires a MySQL PyMySQL database URL")
        tokens = [self.admin_token, self.adapter_token, self.agent_token, self.media_token]
        if any(not token for token in tokens):
            raise RuntimeError("All Fast-Call Adapter API tokens are required")
        if len(set(tokens)) != len(tokens):
            raise RuntimeError("All Fast-Call Adapter API tokens must be different")
        if not self.sip_enabled and not self.mock_calls_enabled:
            raise RuntimeError("At least one of SIP or Mock Call must be enabled")
        if self.sip_enabled and not self.freeswitch_sip_gateway:
            raise RuntimeError("FREESWITCH_SIP_GATEWAY is required when SIP is enabled")


def result_changed_events_enabled():
    value = os.getenv("FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}
