from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentTenant, get_current_tenant
from app.db.session import get_db
from app.models.knowledge import KnowledgeBaseEntry
from app.schemas.knowledge import KnowledgeSummaryResponse, RelatedBriefResponse
from app.services.knowledge_service import get_related_briefs

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


@router.get("", response_model=KnowledgeSummaryResponse)
async def knowledge_summary(tenant: CurrentTenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            func.count(func.distinct(KnowledgeBaseEntry.brief_id)),
            func.max(KnowledgeBaseEntry.created_at),
        ).where(KnowledgeBaseEntry.tenant_id == tenant.tenant_id)
    )
    total_briefs, last_indexed_at = result.one()

    domains = await db.scalars(
        select(KnowledgeBaseEntry.domain).where(KnowledgeBaseEntry.tenant_id == tenant.tenant_id).distinct()
    )
    topics = await db.scalars(
        select(KnowledgeBaseEntry.topic).where(KnowledgeBaseEntry.tenant_id == tenant.tenant_id).distinct()
    )

    return KnowledgeSummaryResponse(
        total_briefs_indexed=total_briefs or 0,
        domains_covered=list(domains),
        topics=list(topics),
        last_indexed_at=last_indexed_at,
    )


@router.get("/related", response_model=list[RelatedBriefResponse])
async def related_briefs(topic: str = Query(...), tenant: CurrentTenant = Depends(get_current_tenant)):
    return get_related_briefs(str(tenant.tenant_id), topic, limit=3)
