import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

DEPTH_MINUTES = {"quick": 3, "standard": 8, "deep": 15}


class BriefCreateRequest(BaseModel):
    topic: str
    scope: str
    domain: str = Field(pattern="^(finance|tech|regulatory|general)$")
    depth: str = Field(default="standard", pattern="^(quick|standard|deep)$")
    audience: str = Field(default="analyst", pattern="^(executive|analyst|technical)$")
    consent_store: bool = False
    email: EmailStr
    alert_threshold: float | None = None


class BriefCreateResponse(BaseModel):
    brief_id: uuid.UUID
    status: str
    estimated_minutes: int


class BriefResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    topic: str
    scope: str
    domain: str
    depth: str
    audience: str
    status: str
    supervisor_plan: dict | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class BriefListResponse(BaseModel):
    items: list[BriefResponse]
    total: int
    page: int
    page_size: int


class AgentTraceResponse(BaseModel):
    id: uuid.UUID
    agent_id: str
    agent_type: str
    llm_provider: str
    llm_model: str
    output_summary: str | None
    status: str
    latency_ms: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BriefPreviewRequest(BaseModel):
    topic: str
    scope: str
    domain: str = Field(pattern="^(finance|tech|regulatory|general)$")


class BriefPreviewResponse(BaseModel):
    complexity: str
    recommended_depth: str
    key_research_questions: list[str]
    potential_data_sources: list[str]
    estimated_minutes: int
    related_briefs_found: int


class BriefResumeRequest(BaseModel):
    action: str = Field(pattern="^(approve|revise|expand)$")
    feedback: str | None = None
    sections_to_revise: list[str] | None = None


class BriefResumeResponse(BaseModel):
    brief_id: uuid.UUID
    status: str = "resume_signal_sent"


class ClaimResponse(BaseModel):
    id: uuid.UUID
    agent_id: str
    claim_text: str
    source_url: str | None
    confidence: float
    verified: bool | None
    disputed_by_agent: str | None
    final_confidence: float | None
    created_at: datetime

    model_config = {"from_attributes": True}
