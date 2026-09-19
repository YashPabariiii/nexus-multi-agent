import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResearchBrief(Base):
    __tablename__ = "research_briefs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    depth: Mapped[str] = mapped_column(String, nullable=False, default="standard")
    audience: Mapped[str] = mapped_column(String, nullable=False, default="analyst")
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    supervisor_plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    agent_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    consent_store: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_email: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
