"""Employment verification letter (PDF) generation — the first of several
planned "custom FAQ" tools that hand back a generated file rather than just
answering from the policy corpus. Self-service only: always generates the
letter for the caller (actor_persona_id), never for anyone else.

Runs inside the MCP subprocess (see app/mcp_server/server.py) — the caller
(app/orchestrator.py, in the main process) is responsible for extracting the
base64 PDF bytes out of build()'s return value and staging them into
app/documents.py, since this subprocess has no way to reach the main
process's memory directly.
"""

import base64
import datetime
from io import BytesIO

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from PIL import Image

from app.mcp_server import hris_store
from app.personas import PERSONAS

LOGO_PATH = "app/static/img/logo.png"
LOGO_DISPLAY_WIDTH = 2.5 * inch


def _prepare_logo() -> tuple[BytesIO, float]:
    """Flattens the RGBA logo onto white and scales it to letterhead size,
    keeping the embedded image (and therefore the base64 payload handed back
    over the MCP subprocess boundary) small — the source file is 1899x335,
    539 KB, too large to embed as-is. Returns (png_buffer, height/width
    aspect ratio) so the caller can size the reportlab Image flowable
    without re-opening the buffer."""
    source = Image.open(LOGO_PATH).convert("RGBA")
    flattened = Image.new("RGB", source.size, "white")
    flattened.paste(source, mask=source.getchannel("A"))

    aspect = source.height / source.width
    target_width_px = 540  # comfortably sharp at LOGO_DISPLAY_WIDTH print size
    target_height_px = round(target_width_px * aspect)
    flattened = flattened.resize((target_width_px, target_height_px), Image.LANCZOS)

    buf = BytesIO()
    flattened.save(buf, format="PNG")
    buf.seek(0)
    return buf, aspect


def _format_date(iso_date: str) -> str:
    return datetime.datetime.strptime(iso_date, "%Y-%m-%d").strftime("%B %d, %Y")


def _build_pdf(persona, record: dict) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=1 * inch,
        rightMargin=1 * inch,
    )

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=11, leading=16, alignment=TA_LEFT, spaceAfter=12
    )

    logo_buf, logo_aspect = _prepare_logo()
    logo_height = LOGO_DISPLAY_WIDTH * logo_aspect

    today_str = datetime.date.today().strftime("%B %d, %Y")
    start_date_str = _format_date(record["start_date"])

    story = [
        RLImage(logo_buf, width=LOGO_DISPLAY_WIDTH, height=logo_height),
        Spacer(1, 0.4 * inch),
        Paragraph(today_str, body_style),
        Spacer(1, 0.2 * inch),
        Paragraph("To Whom It May Concern:", body_style),
        Paragraph(f"<b>RE: Employment Verification — {persona.display_name}</b>", body_style),
        Paragraph(
            f"This letter confirms that {persona.display_name} is currently employed by "
            f"PeopleFabrix as {persona.title} in the {persona.department} department, based "
            f"in our {persona.location_city}, {persona.location_country} office. "
            f"{persona.display_name} has been employed with PeopleFabrix on a "
            f"{record['employment_type']} basis since {start_date_str}, and remains an active "
            f"employee in good standing as of the date of this letter.",
            body_style,
        ),
        Paragraph(
            "This letter has been generated for employment verification purposes. Please "
            "contact our People team if any additional information is required.",
            body_style,
        ),
        Spacer(1, 0.3 * inch),
        Paragraph("Sincerely,", body_style),
        Paragraph("PeopleFabrix People Team", body_style),
    ]

    doc.build(story)
    return buf.getvalue()


def build(actor_persona_id: str) -> dict:
    record = hris_store.read(actor_persona_id, actor_persona_id)
    if "error" in record:
        return record

    persona = PERSONAS[actor_persona_id]
    pdf_bytes = _build_pdf(persona, record)

    return {
        "status": "document_ready",
        "filename": f"employment_verification_{actor_persona_id}.pdf",
        "content_type": "application/pdf",
        "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
    }
