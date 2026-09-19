import io
from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_trace import AgentTrace, Claim
from app.models.brief import ResearchBrief
from app.models.report import Report
from app.services.confidence_service import confidence_label

styles = getSampleStyleSheet()
STYLE_TITLE = ParagraphStyle("NexusTitle", parent=styles["Title"], fontSize=26, spaceAfter=12)
STYLE_H1 = ParagraphStyle("NexusH1", parent=styles["Heading1"], spaceBefore=14, spaceAfter=8)
STYLE_H2 = ParagraphStyle("NexusH2", parent=styles["Heading2"], spaceBefore=10, spaceAfter=6)
STYLE_BODY = ParagraphStyle("NexusBody", parent=styles["BodyText"], fontSize=10.5, leading=15)
STYLE_BULLET = ParagraphStyle("NexusBullet", parent=STYLE_BODY, leftIndent=14, bulletIndent=4)
STYLE_SMALL = ParagraphStyle("NexusSmall", parent=styles["BodyText"], fontSize=8.5, textColor=colors.grey)

CONFIDENCE_COLORS = {
    "HIGH": colors.HexColor("#1a7f37"),
    "MEDIUM": colors.HexColor("#9a6700"),
    "LOW": colors.HexColor("#cf222e"),
    "UNVERIFIED": colors.HexColor("#57606a"),
}

DEBATE_VERDICT_SUFFIX = "_debate_verdict"
DEBATE_DEFENSE_SUFFIX = "_debate_defense"


class _ReportData:
    def __init__(self, brief: ResearchBrief, report: Report, traces: list[AgentTrace], claims: list[Claim]):
        self.brief = brief
        self.report = report
        self.traces = traces
        self.claims = claims


async def _load(db: AsyncSession, brief_id: str) -> _ReportData:
    import uuid

    bid = uuid.UUID(brief_id)
    brief = await db.get(ResearchBrief, bid)
    report = await db.scalar(select(Report).where(Report.brief_id == bid))
    traces = list(await db.scalars(select(AgentTrace).where(AgentTrace.brief_id == bid).order_by(AgentTrace.created_at)))
    claims = list(await db.scalars(select(Claim).where(Claim.brief_id == bid).order_by(Claim.created_at)))
    if brief is None or report is None:
        raise ValueError(f"brief {brief_id} has no completed report yet")
    return _ReportData(brief, report, traces, claims)


def _confidence_badge(score: float) -> Paragraph:
    label = confidence_label(score)
    color = CONFIDENCE_COLORS[label]
    return Paragraph(f'<font color="{color.hexval()}"><b>{label}</b> ({score:.0%})</font>', STYLE_BODY)


def _badge_row(*labels: str) -> Paragraph:
    return Paragraph(" &nbsp;|&nbsp; ".join(f"<b>{label}</b>" for label in labels), STYLE_BODY)


def _cover_page(data: _ReportData) -> list:
    brief, report = data.brief, data.report
    label = confidence_label(report.overall_confidence)
    color = CONFIDENCE_COLORS[label]

    return [
        Paragraph("NEXUS", ParagraphStyle("Logo", parent=STYLE_TITLE, fontSize=36, textColor=colors.HexColor("#1f2937"))),
        Paragraph("Autonomous Research Intelligence Platform", STYLE_SMALL),
        Spacer(1, 0.6 * inch),
        Paragraph(report.title, STYLE_TITLE),
        Spacer(1, 0.15 * inch),
        _badge_row(brief.domain.upper(), brief.depth.upper(), brief.audience.upper()),
        Spacer(1, 0.3 * inch),
        Paragraph(f"Generated: {report.created_at.strftime('%Y-%m-%d %H:%M UTC')}", STYLE_SMALL),
        Spacer(1, 0.4 * inch),
        Paragraph(
            f'<font size="20" color="{color.hexval()}"><b>{label}</b></font> '
            f'<font size="14">confidence &mdash; {report.overall_confidence:.0%}</font>',
            STYLE_BODY,
        ),
        Spacer(1, 0.5 * inch),
        Paragraph("Researched by 7 AI agents", STYLE_SMALL),
        PageBreak(),
    ]


def _stats_box(data: _ReportData) -> Table:
    brief, traces, claims = data.brief, data.traces, data.claims

    total_sources = len({c.source_url for c in claims if c.source_url})
    verified = sum(1 for c in claims if c.verified)
    disputed = sum(1 for c in claims if c.disputed_by_agent)
    debate_rounds = sum(1 for t in traces if t.agent_type.endswith(DEBATE_VERDICT_SUFFIX))

    duration = ""
    if brief.completed_at and brief.created_at:
        secs = int((brief.completed_at - brief.created_at).total_seconds())
        duration = f"{secs // 60}min {secs % 60}sec"

    rows = [
        ["Total sources", str(total_sources), "Claims verified", str(verified)],
        ["Claims disputed", str(disputed), "Debate rounds", str(debate_rounds)],
        ["Research duration", duration or "n/a", "LLM providers", "Groq + Gemini"],
    ]
    table = Table(rows, colWidths=[1.6 * inch, 1.1 * inch, 1.6 * inch, 1.1 * inch])
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                ("TEXTCOLOR", (2, 0), (2, -1), colors.grey),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                ("FONTNAME", (3, 0), (3, -1), "Helvetica-Bold"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.whitesmoke),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _executive_summary_page(data: _ReportData, related_briefs: list[dict]) -> list:
    story = [Paragraph("Executive Summary", STYLE_H1)]

    for line in data.report.executive_summary.splitlines():
        line = line.strip().lstrip("-").strip()
        if line:
            story.append(Paragraph(f"&bull; {line}", STYLE_BULLET))

    story.append(Spacer(1, 0.2 * inch))
    agents_used = sorted({t.agent_type for t in data.traces if "_debate_" not in t.agent_type})
    story.append(Paragraph("Agent team used: " + ", ".join(agents_used), STYLE_SMALL))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Research stats", STYLE_H2))
    story.append(_stats_box(data))

    if related_briefs:
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("Related past research", STYLE_H2))
        for rb in related_briefs[:3]:
            story.append(Paragraph(f"Similar to: {rb.get('topic')} ({rb.get('date', '')[:10]})", STYLE_BULLET))

    story.append(PageBreak())
    return story


def _sections_pages(data: _ReportData) -> list:
    story = []
    disputed_claims = [c for c in data.claims if c.disputed_by_agent]

    for section in data.report.sections:
        story.append(Paragraph(section.get("title", "Untitled Section"), STYLE_H1))
        story.append(_confidence_badge(section.get("confidence", 0.0)))
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph(section.get("content", ""), STYLE_BODY))

        citations = section.get("citations") or []
        if citations:
            story.append(Spacer(1, 0.15 * inch))
            story.append(Paragraph("Citations", STYLE_H2))
            for c in citations:
                title = c.get("title") or "Untitled source"
                url = c.get("url") or ""
                story.append(Paragraph(f"&bull; {title} — <font color='#0969da'>{url}</font>", STYLE_SMALL))

        story.append(PageBreak())

    if disputed_claims:
        story.append(Paragraph("Disputed Claims", STYLE_H1))
        rows = [["Claim", "Original", "Final", "Reason"]]
        for c in disputed_claims:
            rows.append(
                [
                    Paragraph(c.claim_text[:120], STYLE_SMALL),
                    f"{c.confidence:.0%}",
                    f"{(c.final_confidence or 0):.0%}",
                    Paragraph((c.dispute_reason or "")[:120], STYLE_SMALL),
                ]
            )
        table = Table(rows, colWidths=[2.6 * inch, 0.7 * inch, 0.6 * inch, 2.1 * inch], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
        story.append(PageBreak())

    return story


def _agent_intelligence_page(data: _ReportData) -> list:
    story = [Paragraph("Agent Intelligence Log", STYLE_H1)]

    by_agent: dict[str, dict] = {}
    for t in data.traces:
        if "_debate_" in t.agent_type:
            continue
        bucket = by_agent.setdefault(t.agent_type, {"provider": t.llm_provider, "claims": 0, "disputes": 0, "tokens": 0, "latency": 0})
        bucket["claims"] += len(t.claims or [])
        bucket["tokens"] += (t.prompt_tokens or 0) + (t.completion_tokens or 0)
        bucket["latency"] += t.latency_ms or 0

    for c in data.claims:
        if c.disputed_by_agent and c.agent_id in by_agent:
            by_agent[c.agent_id]["disputes"] += 1

    rows = [["Agent", "Provider", "Claims", "Disputes", "Tokens", "Latency (ms)"]]
    for agent_type, stats in sorted(by_agent.items()):
        rows.append([agent_type, stats["provider"], str(stats["claims"]), str(stats["disputes"]), str(stats["tokens"]), str(stats["latency"])])

    table = Table(rows, colWidths=[1.4 * inch, 0.9 * inch, 0.8 * inch, 0.8 * inch, 0.8 * inch, 1.1 * inch], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
            ]
        )
    )
    story.append(table)

    debate_verdicts = [t for t in data.traces if t.agent_type.endswith(DEBATE_VERDICT_SUFFIX)]
    if debate_verdicts:
        story.append(Spacer(1, 0.25 * inch))
        story.append(Paragraph("Debate Summary", STYLE_H2))
        rows = [["Claim / Input", "Resolution", "Final confidence"]]
        for t in debate_verdicts:
            conf = (t.confidence_scores or {}).get("final_confidence", 0.0)
            rows.append([Paragraph((t.input_summary or "")[:150], STYLE_SMALL), Paragraph((t.output_summary or "")[:150], STYLE_SMALL), f"{conf:.0%}"])
        debate_table = Table(rows, colWidths=[2.4 * inch, 2.4 * inch, 1.2 * inch], repeatRows=1)
        debate_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(debate_table)

    story.append(PageBreak())
    return story


def _methodology_page() -> list:
    return [
        Paragraph("Methodology", STYLE_H1),
        Paragraph("Agent roles", STYLE_H2),
        Paragraph(
            "<b>Supervisor</b> plans research tasks and re-plans around gaps. "
            "<b>Web search / Domain knowledge / Data analyst</b> gather claims from the web, the tenant's "
            "knowledge base, and computed metrics respectively. <b>Fact-checker</b> adversarially reviews every "
            "claim and can trigger a debate round with the originating agent. <b>Synthesis</b> structures verified "
            "claims into report sections. <b>Writer</b> produces the final prose per audience.",
            STYLE_BODY,
        ),
        Paragraph("Confidence scoring", STYLE_H2),
        Paragraph(
            "Each claim starts at the source agent's self-reported confidence, then is adjusted for trusted-domain "
            "sourcing, corroboration across independent agents, and dispute/retraction penalties from fact-checking "
            "or debate. Section confidence is the importance-weighted average of its claims; report confidence is "
            "the word-count-weighted average of its sections.",
            STYLE_BODY,
        ),
        Paragraph("Source quality criteria", STYLE_H2),
        Paragraph(
            "Sources are weighted higher when they fall within the domain's trusted-domain allowlist "
            "(e.g. sec.gov/bloomberg/reuters for finance) and lower when uncorroborated or contradicted.",
            STYLE_BODY,
        ),
        Paragraph("Limitations", STYLE_H2),
        Paragraph(
            "Research reflects publicly available web sources at generation time and the tenant's own knowledge "
            "base. Disputed and low-confidence claims are flagged, not silently dropped — read the disputed-claims "
            "table before relying on any UNVERIFIED or LOW-confidence finding.",
            STYLE_BODY,
        ),
        HRFlowable(width="100%", color=colors.lightgrey),
    ]


async def generate_report_pdf(db: AsyncSession, brief_id: str, related_briefs: list[dict] | None = None) -> bytes:
    data = await _load(db, brief_id)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.7 * inch, bottomMargin=0.7 * inch)

    story = []
    story += _cover_page(data)
    story += _executive_summary_page(data, related_briefs or [])
    story += _sections_pages(data)
    story += _agent_intelligence_page(data)
    story += _methodology_page()

    doc.build(story)
    return buffer.getvalue()


async def generate_trace_pdf(db: AsyncSession, brief_id: str) -> bytes:
    data = await _load(db, brief_id)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.7 * inch, bottomMargin=0.7 * inch)

    story = [
        Paragraph(f"Agent Trace Log — {data.report.title}", STYLE_TITLE),
        Paragraph(f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}", STYLE_SMALL),
        Spacer(1, 0.2 * inch),
        Paragraph("Timeline", STYLE_H1),
    ]

    timeline_rows = [["#", "Agent", "Started", "Latency (ms)"]]
    for i, t in enumerate(data.traces, 1):
        timeline_rows.append([str(i), t.agent_type, t.created_at.strftime("%H:%M:%S"), str(t.latency_ms or "")])
    timeline_table = Table(timeline_rows, colWidths=[0.4 * inch, 1.8 * inch, 1.2 * inch, 1.2 * inch], repeatRows=1)
    timeline_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
            ]
        )
    )
    story.append(timeline_table)
    story.append(PageBreak())

    for i, t in enumerate(data.traces, 1):
        story.append(Paragraph(f"{i}. {t.agent_type}", STYLE_H1))
        story.append(Paragraph(f"{t.llm_provider} / {t.llm_model} &mdash; status={t.status} &mdash; {t.latency_ms}ms", STYLE_SMALL))
        story.append(Spacer(1, 0.1 * inch))

        story.append(Paragraph("Input", STYLE_H2))
        story.append(Paragraph(t.input_summary or "(none)", STYLE_BODY))

        story.append(Paragraph("Reasoning", STYLE_H2))
        story.append(Paragraph((t.reasoning_trace or "(none)")[:3000], STYLE_SMALL))

        story.append(Paragraph("Output", STYLE_H2))
        story.append(Paragraph(t.output_summary or "(none)", STYLE_BODY))

        if t.tool_calls:
            story.append(Paragraph("Tool calls", STYLE_H2))
            for call in t.tool_calls:
                story.append(Paragraph(f"&bull; {call}", STYLE_SMALL))

        if t.claims:
            story.append(Paragraph("Claims produced", STYLE_H2))
            for c in t.claims:
                story.append(Paragraph(f"&bull; {c.get('claim', '')} (confidence={c.get('confidence', 0):.2f})", STYLE_SMALL))

        if t.confidence_scores:
            story.append(Paragraph(f"Confidence after fact-check: {t.confidence_scores}", STYLE_SMALL))

        story.append(PageBreak())

    doc.build(story)
    return buffer.getvalue()
