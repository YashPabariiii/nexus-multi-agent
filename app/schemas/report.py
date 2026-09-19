import uuid

from pydantic import BaseModel, EmailStr


class ReportEmailRequest(BaseModel):
    email: EmailStr


class ReportEmailResponse(BaseModel):
    status: str = "email_queued"


class CompareBriefsRequest(BaseModel):
    brief_id_a: uuid.UUID
    brief_id_b: uuid.UUID


class CompareBriefsResponse(BaseModel):
    common_findings: list[str]
    contradictions: list[str]
    new_developments: list[str]
    confidence_delta: float
