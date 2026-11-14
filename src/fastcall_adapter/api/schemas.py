import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReviewRequest(RequestModel):
    expected_state_version: int = Field(ge=1)
    resolution: str
    result_code: str | None = Field(default=None, max_length=64)
    evidence_type: str = Field(min_length=1, max_length=64)
    evidence_ref: str = Field(min_length=1, max_length=255)
    note: str = Field(min_length=1, max_length=2000)
    force_release: bool = False


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
