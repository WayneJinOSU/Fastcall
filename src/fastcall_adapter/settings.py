import os
from dataclasses import dataclass


SUPPORTED_ENVIRONMENTS = {"dev", "test", "prod"}


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    collection_agent_ws_url: str = "ws://127.0.0.1:8765/ws-v2"
    admin_token: str = ""
    adapter_token: str = ""
    agent_token: str = ""
    media_token: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            app_env=os.getenv("APP_ENV", "").strip(),
            database_url=(
                os.getenv("ROBOT_CONFIG_DATABASE_URL", "").strip()
                or os.getenv("DATABASE_URL", "").strip()
            ),
            collection_agent_ws_url=(
                os.getenv("COLLECTION_AGENT_WS_URL", "ws://127.0.0.1:8765/ws-v2").strip()
            ),
            admin_token=os.getenv("FASTCALL_ADMIN_TOKEN", "").strip(),
            adapter_token=os.getenv("FASTCALL_ADAPTER_TOKEN", "").strip(),
            agent_token=os.getenv("FASTCALL_AGENT_TOKEN", "").strip(),
            media_token=os.getenv("FASTCALL_MEDIA_TOKEN", "").strip(),
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
            raise RuntimeError("Fast-Call Adapter API tokens must be different")


def result_changed_events_enabled():
    value = os.getenv("FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}
