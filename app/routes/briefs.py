import json
import uuid

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner import assess_brief_complexity
from app.config.settings import get_settings
from app.core.auth import CurrentTenant, check_brief_limit, check_depth_limit, get_current_tenant
from app.core.metrics import briefs_submitted_total, human_reviews_total, limiter, tier_limit_hits_total
from app.db.session import get_db
from app.models.agent_trace import AgentTrace, Claim
from app.models.brief import ResearchBrief
from app.models.tenant import Tenant
from app.schemas.brief import (
    DEPTH_MINUTES,
    AgentTraceResponse,
    BriefCreateRequest,
    BriefCreateResponse,
    BriefListResponse,
    BriefPreviewRequest,
    BriefPreviewResponse,
    BriefResponse,
    BriefResumeRequest,
    BriefResumeResponse,
    ClaimResponse,
)
from app.services.knowledge_service import get_related_briefs
from app.workers.tasks import run_research_brief

router = APIRouter(prefix="/v1/briefs", tags=["briefs"])
settings = get_settings()


async def _get_owned_brief(brief_id: uuid.UUID, tenant: CurrentTenant, db: AsyncSession) -> ResearchBrief:
    brief = await db.scalar(select(ResearchBrief).where(ResearchBrief.id == brief_id, ResearchBrief.tenant_id == tenant.tenant_id))
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brief not found")
    return brief


@router.post("", response_model=BriefCreateResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def create_brief(
    request: Request,
    payload: BriefCreateRequest,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    db_tenant = await db.get(Tenant, tenant.tenant_id)
    if db_tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    try:
        check_brief_limit(db_tenant)
        check_depth_limit(db_tenant, payload.depth)
    except HTTPException as exc:
        limit_type = "brief_count" if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS else "depth"
        tier_limit_hits_total.labels(plan=db_tenant.plan, limit_type=limit_type).inc()
        raise

    brief = ResearchBrief(
        tenant_id=tenant.tenant_id,
        topic=payload.topic,
        scope=payload.scope,
        domain=payload.domain,
        depth=payload.depth,
        audience=payload.audience,
        status="queued",
        consent_store=payload.consent_store,
        user_email=payload.email,
    )
    db.add(brief)
    db_tenant.brief_count_this_month += 1
    await db.commit()
    await db.refresh(brief)

    run_research_brief.delay(str(brief.id), str(tenant.tenant_id))
    briefs_submitted_total.labels(domain=payload.domain, depth=payload.depth, plan=db_tenant.plan).inc()

    return BriefCreateResponse(brief_id=brief.id, status=brief.status, estimated_minutes=DEPTH_MINUTES[payload.depth])


@router.post("/preview", response_model=BriefPreviewResponse)
@limiter.limit("20/minute")
async def preview_brief(request: Request, payload: BriefPreviewRequest, tenant: CurrentTenant = Depends(get_current_tenant)):
    complexity = await assess_brief_complexity(payload.topic, payload.scope, payload.domain)
    related = get_related_briefs(str(tenant.tenant_id), payload.topic, limit=3)

    return BriefPreviewResponse(
        complexity=complexity.get("complexity", "medium"),
        recommended_depth=complexity.get("recommended_depth", "standard"),
        key_research_questions=complexity.get("key_research_questions", []),
        potential_data_sources=complexity.get("potential_data_sources", []),
        estimated_minutes=complexity.get("estimated_minutes", DEPTH_MINUTES["standard"]),
        related_briefs_found=len(related),
    )


@router.post("/{brief_id}/resume", response_model=BriefResumeResponse)
@limiter.limit("20/minute")
async def resume_brief(
    request: Request,
    brief_id: uuid.UUID,
    payload: BriefResumeRequest,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    brief = await _get_owned_brief(brief_id, tenant, db)
    if brief.status != "awaiting_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Brief is not awaiting review (status={brief.status})")

    client = redis.from_url(settings.REDIS_URL)
    try:
        await client.publish(
            f"brief:{brief_id}:resume",
            json.dumps({"action": payload.action, "feedback": payload.feedback, "sections_to_revise": payload.sections_to_revise}),
        )
    finally:
        await client.aclose()

    human_reviews_total.labels(action=payload.action).inc()
    return BriefResumeResponse(brief_id=brief_id)


@router.get("", response_model=BriefListResponse)
async def list_briefs(
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    total = await db.scalar(select(func.count()).select_from(ResearchBrief).where(ResearchBrief.tenant_id == tenant.tenant_id))
    result = await db.scalars(
        select(ResearchBrief)
        .where(ResearchBrief.tenant_id == tenant.tenant_id)
        .order_by(ResearchBrief.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return BriefListResponse(items=list(result), total=total or 0, page=page, page_size=page_size)


@router.get("/{brief_id}", response_model=BriefResponse)
async def get_brief(brief_id: uuid.UUID, tenant: CurrentTenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    return await _get_owned_brief(brief_id, tenant, db)


@router.get("/{brief_id}/traces", response_model=list[AgentTraceResponse])
async def get_brief_traces(brief_id: uuid.UUID, tenant: CurrentTenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    await _get_owned_brief(brief_id, tenant, db)
    result = await db.scalars(select(AgentTrace).where(AgentTrace.brief_id == brief_id).order_by(AgentTrace.created_at))
    return list(result)


@router.get("/{brief_id}/claims", response_model=list[ClaimResponse])
async def get_brief_claims(
    brief_id: uuid.UUID,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    verified_only: bool = False,
    disputed_only: bool = False,
):
    await _get_owned_brief(brief_id, tenant, db)
    query = select(Claim).where(Claim.brief_id == brief_id)
    if verified_only:
        query = query.where(Claim.verified.is_(True))
    if disputed_only:
        query = query.where(Claim.disputed_by_agent.is_not(None))
    result = await db.scalars(query.order_by(Claim.created_at))
    return list(result)


@router.delete("/{brief_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brief(brief_id: uuid.UUID, tenant: CurrentTenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    brief = await _get_owned_brief(brief_id, tenant, db)
    await db.delete(brief)
    await db.commit()
