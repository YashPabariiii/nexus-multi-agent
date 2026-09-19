"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("api_key_hash", sa.String(), nullable=False),
        sa.Column("plan", sa.String(), nullable=False, server_default="free"),
        sa.Column("brief_count_this_month", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("onboarded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "research_briefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("depth", sa.String(), nullable=False, server_default="standard"),
        sa.Column("audience", sa.String(), nullable=False, server_default="analyst"),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("supervisor_plan", postgresql.JSONB(), nullable=True),
        sa.Column("agent_config", postgresql.JSONB(), nullable=True),
        sa.Column("consent_store", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("user_email", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_research_briefs_tenant_id", "research_briefs", ["tenant_id"])

    op.create_table(
        "agent_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("brief_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_briefs.id"), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("agent_type", sa.String(), nullable=False),
        sa.Column("llm_provider", sa.String(), nullable=False),
        sa.Column("llm_model", sa.String(), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("reasoning_trace", sa.Text(), nullable=True),
        sa.Column("claims", postgresql.JSONB(), nullable=True),
        sa.Column("confidence_scores", postgresql.JSONB(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_traces_brief_id", "agent_traces", ["brief_id"])

    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("brief_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_briefs.id"), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("source_title", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=True),
        sa.Column("disputed_by_agent", sa.String(), nullable=True),
        sa.Column("dispute_reason", sa.Text(), nullable=True),
        sa.Column("final_confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_claims_brief_id", "claims", ["brief_id"])

    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("brief_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_briefs.id"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("executive_summary", sa.Text(), nullable=False),
        sa.Column("sections", postgresql.JSONB(), nullable=False),
        sa.Column("overall_confidence", sa.Float(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pdf_path", sa.String(), nullable=True),
        sa.Column("emailed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_reports_brief_id", "reports", ["brief_id"])
    op.create_index("ix_reports_tenant_id", "reports", ["tenant_id"])

    op.create_table(
        "knowledge_base_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("brief_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_briefs.id"), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("content_chunk", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column("chroma_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_knowledge_base_entries_tenant_id", "knowledge_base_entries", ["tenant_id"])
    op.create_index("ix_knowledge_base_entries_brief_id", "knowledge_base_entries", ["brief_id"])


def downgrade() -> None:
    op.drop_table("knowledge_base_entries")
    op.drop_table("reports")
    op.drop_table("claims")
    op.drop_table("agent_traces")
    op.drop_table("research_briefs")
    op.drop_table("tenants")
