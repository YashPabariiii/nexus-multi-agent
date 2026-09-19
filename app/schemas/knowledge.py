from datetime import datetime

from pydantic import BaseModel


class KnowledgeSummaryResponse(BaseModel):
    total_briefs_indexed: int
    domains_covered: list[str]
    topics: list[str]
    last_indexed_at: datetime | None


class RelatedBriefResponse(BaseModel):
    brief_id: str
    topic: str | None
    date: str | None
    similarity: float | None
    key_findings_preview: str
