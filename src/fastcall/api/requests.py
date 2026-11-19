import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateAppRequest(RequestModel):
    app_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    name: str = Field(min_length=1, max_length=255)
    config: dict | None = None


class DraftRequest(RequestModel):
    draft_revision: int = Field(ge=1)
    config: dict


class EnabledRequest(RequestModel):
    enabled: bool


class RevisionRequest(RequestModel):
    draft_revision: int = Field(ge=1)


class PublishRequest(RevisionRequest):
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RollbackRequest(RequestModel):
    robot_app_version_id: str = Field(min_length=1, max_length=40)


class ResolveRequest(RequestModel):
    robot_code: str = Field(min_length=1, max_length=128)


class VoiceTestSessionRequest(RequestModel):
    draft_revision: int = Field(ge=1)
    variables: dict = Field(default_factory=dict)


class CallAnalysisRequest(RequestModel):
    business_result: str | None = Field(default=None, max_length=255)
    analysis: dict = Field(default_factory=dict)


class AgentSessionEventRequest(RequestModel):
    event_id: str = Field(min_length=1, max_length=255)
    claim_generation: int = Field(ge=1)
    agent_version: str = Field(min_length=1, max_length=128)
    occurred_at: datetime.datetime
    detail: dict = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_offset(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include an offset")
        return value
