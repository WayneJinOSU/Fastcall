import os
from dataclasses import dataclass


SUPPORTED_ENVIRONMENTS = {"dev", "test", "prod"}


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

    @classmethod
    def from_env(cls):
        return cls(
            app_env=os.getenv("APP_ENV", "").strip(),
            database_url=(
                os.getenv("ROBOT_CONFIG_DATABASE_URL", "").strip()
                or os.getenv("DATABASE_URL", "").strip()
            ),
            management_token=os.getenv("ROBOT_CONFIG_MANAGEMENT_TOKEN", "").strip(),
            resolve_token=os.getenv("ROBOT_CONFIG_RESOLVE_TOKEN", "").strip(),
            console_admin_actor_id=os.getenv("ROBOT_CONFIG_CONSOLE_ADMIN_ACTOR_ID", "").strip(),
            console_admin_password_hash=os.getenv("ROBOT_CONFIG_CONSOLE_ADMIN_PASSWORD_HASH", "").strip(),
            console_session_secret=os.getenv("ROBOT_CONFIG_CONSOLE_SESSION_SECRET", "").strip(),
            collection_agent_ws_url=(
                os.getenv("COLLECTION_AGENT_WS_URL", "ws://127.0.0.1:8765/ws-v2").strip()
            ),
        )

    def validate(self):
        if self.app_env not in SUPPORTED_ENVIRONMENTS:
            raise RuntimeError("APP_ENV must be one of: dev, test, prod")
        if not self.database_url.startswith("mysql+pymysql://"):
            raise RuntimeError("Robot Config Service requires a MySQL PyMySQL database URL")
        if not self.management_token or not self.resolve_token:
            raise RuntimeError("Both Robot Config Service tokens are required")
        if self.management_token == self.resolve_token:
            raise RuntimeError("Management and Resolve tokens must be different")
        if not self.console_admin_actor_id or not self.console_admin_password_hash:
            raise RuntimeError("Console administrator identity and password hash are required")
        if len(self.console_session_secret) < 32:
            raise RuntimeError("Console session secret must contain at least 32 characters")
