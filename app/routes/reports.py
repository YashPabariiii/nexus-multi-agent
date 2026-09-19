import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentTenant, get_current_tenant
from app.core.metrics import limiter
from app.db.session import get_db
from app.models.report import Report
from app.routes.briefs import _get_owned_brief
from app.schemas.report import (
    CompareBriefsRequest,
    CompareBriefsResponse,
    ReportEmailRequest,
    ReportEmailResponse,
)
from app.services.export_service import build_report_json, build_report_preview
from app.services.knowledge_service import compare_briefs
from app.workers.tasks import generate_report, trace_pdf_path

router = APIRouter(prefix="/v1/briefs", tags=["reports"])


async def _get_report(brief_id: uuid.UUID, tenant: CurrentTenant, db: AsyncSession) -> Report:
    await _get_owned_brief(brief_id, tenant, db)
    report = await db.scalar(select(Report).where(Report.brief_id == brief_id))
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not generated yet")
    return report


@router.get("/{brief_id}/report/download/pdf")
async def download_report_pdf(brief_id: uuid.UUID, tenant: CurrentTenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    report = await _get_report(brief_id, tenant, db)
    if not report.pdf_path or not os.path.exists(report.pdf_path):  # noqa: ASYNC240 (cheap local stat)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not generated yet")
    return FileResponse(report.pdf_path, media_type="application/pdf", filename=f"{brief_id}-report.pdf")


@router.get("/{brief_id}/report/download/trace")
async def download_trace_pdf(brief_id: uuid.UUID, tenant: CurrentTenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    report = await _get_report(brief_id, tenant, db)
    if not report.pdf_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trace PDF not generated yet")
    path = trace_pdf_path(report.pdf_path)
    if not os.path.exists(path):  # noqa: ASYNC240 (cheap local stat)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trace PDF not generated yet")
    return FileResponse(path, media_type="application/pdf", filename=f"{brief_id}-trace.pdf")


@router.get("/{brief_id}/report/download/json")
async def download_report_json(brief_id: uuid.UUID, tenant: CurrentTenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    await _get_owned_brief(brief_id, tenant, db)
    data = await build_report_json(db, str(brief_id))
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not generated yet")
    return data


@router.get("/{brief_id}/report/preview")
async def preview_report(brief_id: uuid.UUID, tenant: CurrentTenant = Depends(get_current_tenant), db: AsyncSession = Depends(get_db)):
    await _get_owned_brief(brief_id, tenant, db)
    data = await build_report_preview(db, str(brief_id))
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not generated yet")
    return data


@router.post("/{brief_id}/report/email", response_model=ReportEmailResponse)
@limiter.limit("20/minute")
async def resend_report_email(
    request: Request,
    brief_id: uuid.UUID,
    payload: ReportEmailRequest,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    await _get_report(brief_id, tenant, db)
    generate_report.delay(str(brief_id), payload.email)
    return ReportEmailResponse()


@router.post("/compare", response_model=CompareBriefsResponse)
@limiter.limit("20/minute")
async def compare_two_briefs(
    request: Request,
    payload: CompareBriefsRequest,
    tenant: CurrentTenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_brief(payload.brief_id_a, tenant, db)
    await _get_owned_brief(payload.brief_id_b, tenant, db)

    result = await compare_briefs(db, str(payload.brief_id_a), str(payload.brief_id_b))
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["error"])

    return CompareBriefsResponse(**result)
