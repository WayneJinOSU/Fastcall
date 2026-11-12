import copy
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

from robot_config_service.application.robot_apps import RobotAppsUseCase
from robot_config_service.domain.errors import RobotConfigError


SESSION_TTL_SECONDS = 60


def _sample_variable(variable: dict):
    key = variable.get("key", "")
    semantic_samples = {
        "user_name": "Test User",
        "app_type": "Test App",
    }
    if key in semantic_samples:
        return semantic_samples[key]
    return {
        "decimal": "128.50",
        "integer": 1,
        "boolean": True,
        "datetime": "2030-01-01T10:00:00",
        "string": "Sample",
    }.get(variable.get("type"), "Sample")


def build_voice_test_variables(definitions, supplied) -> dict:
    supplied = supplied if isinstance(supplied, dict) else {}
    values = {}
    for definition in definitions if isinstance(definitions, list) else []:
        if not isinstance(definition, dict) or not definition.get("key"):
            continue
        key = definition["key"]
        value = supplied.get(key)
        if value is None or value == "":
            value = definition.get("default")
        if value is None or value == "":
            value = _sample_variable(definition)
        values[key] = value
    return values


@dataclass
class VoiceTestSession:
    session_id: str
    ticket: str
    actor: str
    app_id: str
    config: dict
    variables: dict
    runtime_engine: str
    config_hash: str
    call_uuid: str
    expires_at: float
    claimed: bool = False

    def start_payload(self) -> dict:
        return {
            "action": "start",
            "data": {
                "callUUID": self.call_uuid,
                "callee": "console-test",
                "config": copy.deepcopy(self.config),
                "variables": copy.deepcopy(self.variables),
            },
        }


class VoiceTestSessionStore:
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    def create(self, *, actor, app_id, config, variables, runtime_engine, config_hash) -> VoiceTestSession:
        with self._lock:
            now = time.time()
            self._sessions = {
                key: value for key, value in self._sessions.items() if value.expires_at > now
            }
            session = VoiceTestSession(
                session_id=f"voice-test-{uuid.uuid4().hex}",
                ticket=secrets.token_urlsafe(32),
                actor=actor,
                app_id=app_id,
                config=copy.deepcopy(config),
                variables=copy.deepcopy(variables),
                runtime_engine=runtime_engine,
                config_hash=config_hash,
                call_uuid=f"console-test-{uuid.uuid4().hex}",
                expires_at=now + SESSION_TTL_SECONDS,
            )
            self._sessions[session.session_id] = session
            return session

    def claim(self, session_id: str, ticket: str, actor: str) -> VoiceTestSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if (
                session is None
                or session.expires_at <= time.time()
                or session.claimed
                or session.actor != actor
                or not secrets.compare_digest(session.ticket, ticket)
            ):
                raise RobotConfigError(
                    "VOICE_TEST_SESSION_INVALID",
                    "Voice test session is invalid, expired or already used",
                )
            session.claimed = True
            return session

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


class VoiceTestUseCase:
    def __init__(self, uow, validator, store: VoiceTestSessionStore):
        self.uow = uow
        self.validator = validator
        self.store = store

    def create(self, app_id, draft_revision, supplied_variables, actor):
        app = RobotAppsUseCase(self.uow).get(app_id)
        report = RobotAppsUseCase(self.uow).validate(app_id, draft_revision, self.validator)
        if not report["valid"]:
            raise RobotConfigError(
                "CONFIG_VALIDATION_FAILED",
                "Voice test requires a valid draft configuration",
                details=report["errors"],
            )
        config = app["draft"]
        runtime = report.get("runtime_resolution") or {}
        session = self.store.create(
            actor=actor,
            app_id=app_id,
            config=config,
            variables=build_voice_test_variables(config.get("variables"), supplied_variables),
            runtime_engine=runtime.get("engine", "standard"),
            config_hash=report.get("config_hash") or "",
        )
        return {
            "session_id": session.session_id,
            "ticket": session.ticket,
            "ws_url": f"/console/api/v1/voice-test/{session.session_id}/ws?{urlencode({'ticket': session.ticket})}",
            "expires_at": int(session.expires_at),
            "runtime_engine": session.runtime_engine,
            "config_hash": session.config_hash,
            "call_uuid": session.call_uuid,
            "variables": copy.deepcopy(session.variables),
        }
