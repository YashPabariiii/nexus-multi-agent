import asyncio
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config.settings import get_settings
from app.core.logging import logger
from app.services.confidence_service import confidence_label

settings = get_settings()

LOW_CONFIDENCE_THRESHOLD = settings.LOW_CONFIDENCE_ALERT_THRESHOLD


def _send_sync(msg: MIMEMultipart, to: str) -> None:
    if not settings.MAIL_SERVER:
        logger.warning("email_skipped_no_smtp_configured", to=to, subject=msg["Subject"])
        return

    with smtplib.SMTP(settings.MAIL_SERVER, settings.MAIL_PORT) as server:
        server.starttls()
        if settings.MAIL_USERNAME:
            server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
        server.sendmail(settings.MAIL_FROM or settings.MAIL_USERNAME, [to], msg.as_string())


async def _send(msg: MIMEMultipart, to: str) -> None:
    try:
        await asyncio.to_thread(_send_sync, msg, to)
        logger.info("email_sent", to=to, subject=msg["Subject"])
    except Exception as exc:
        logger.error("email_send_failed", to=to, subject=msg["Subject"], error=str(exc))


def _attach_pdf(msg: MIMEMultipart, filename: str, pdf_bytes: bytes) -> None:
    part = MIMEApplication(pdf_bytes, _subtype="pdf")
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)


async def send_brief_ready(
    to: str,
    brief_metadata: dict,
    pdf_bytes: bytes,
    trace_pdf_bytes: bytes,
) -> None:
    topic = brief_metadata.get("topic", "Research Brief")
    confidence = brief_metadata.get("overall_confidence", 0.0)
    label = confidence_label(confidence)
    is_alert = confidence < LOW_CONFIDENCE_THRESHOLD

    subject = f"⚠️ Nexus Research Brief — Low Confidence Warning: {topic}" if is_alert else f"Nexus Research Brief Ready: {topic}"

    html = f"""
    <h2>{topic}</h2>
    <p><b>{label}</b> confidence &mdash; {confidence:.0%}</p>
    <p>{brief_metadata.get('executive_summary', '').replace(chr(10), '<br>')}</p>
    <p>Agent team: {', '.join(brief_metadata.get('agents_used', []))}</p>
    """

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = settings.MAIL_FROM or settings.MAIL_USERNAME
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    _attach_pdf(msg, "report.pdf", pdf_bytes)
    _attach_pdf(msg, "agent_trace.pdf", trace_pdf_bytes)

    await _send(msg, to)


async def send_human_review_notification(to: str, brief_id: str, topic: str, preview_url: str) -> None:
    msg = MIMEMultipart()
    msg["Subject"] = "Action Required: Review your Nexus research brief"
    msg["From"] = settings.MAIL_FROM or settings.MAIL_USERNAME
    msg["To"] = to
    msg.attach(
        MIMEText(
            f"<p>Your draft brief on <b>{topic}</b> is ready for review.</p>"
            f'<p><a href="{preview_url}">Review &amp; Approve</a></p>',
            "html",
        )
    )
    await _send(msg, to)


async def send_knowledge_digest(to: str, tenant_id: str, digest: dict) -> None:
    msg = MIMEMultipart()
    msg["Subject"] = "Your weekly Nexus knowledge digest"
    msg["From"] = settings.MAIL_FROM or settings.MAIL_USERNAME
    msg["To"] = to
    top_insights = "".join(f"<li>{i}</li>" for i in digest.get("top_insights", [])[:3])
    msg.attach(
        MIMEText(
            f"<p>{digest.get('briefs_completed', 0)} briefs completed this week.</p>"
            f"<p>Topics added: {', '.join(digest.get('topics_added', []))}</p>"
            f"<p>Knowledge base size: {digest.get('knowledge_base_size', 0)} entries</p>"
            f"<ul>{top_insights}</ul>",
            "html",
        )
    )
    await _send(msg, to)
