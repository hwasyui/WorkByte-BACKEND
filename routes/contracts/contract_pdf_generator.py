import io
import json
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.units import cm, mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.platypus.frames import Frame
from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate


NAVY       = colors.HexColor("#1A2E4A")
DARK_NAVY  = colors.HexColor("#0F1E30")
ACCENT     = colors.HexColor("#2E6DA4")
LIGHT_GRAY = colors.HexColor("#F5F5F5")
MID_GRAY   = colors.HexColor("#CCCCCC")
TEXT_DARK  = colors.HexColor("#1F1F1F")
TEXT_MID   = colors.HexColor("#444444")
WHITE      = colors.white

PAGE_W, PAGE_H = A4
MARGIN_H = 2 * cm
MARGIN_V = 2.5 * cm
CONTENT_W = PAGE_W - 2 * MARGIN_H


def _draw_page_decorations(canvas, doc, contract_id, generated_at):
    canvas.saveState()

    # Top banner
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_H - 1.6 * cm, PAGE_W, 1.6 * cm, fill=1, stroke=0)

    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(MARGIN_H, PAGE_H - 1.05 * cm, "FREELANCE CONTRACT AGREEMENT")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(PAGE_W - MARGIN_H, PAGE_H - 1.05 * cm, f"ID: {contract_id}")

    # Bottom footer bar
    canvas.setFillColor(LIGHT_GRAY)
    canvas.rect(0, 0, PAGE_W, 1.2 * cm, fill=1, stroke=0)
    canvas.setStrokeColor(MID_GRAY)
    canvas.setLineWidth(0.5)
    canvas.line(0, 1.2 * cm, PAGE_W, 1.2 * cm)

    canvas.setFillColor(TEXT_MID)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(MARGIN_H, 0.45 * cm, f"Generated: {generated_at}   ·   Confidential")
    canvas.drawRightString(
        PAGE_W - MARGIN_H, 0.45 * cm,
        f"Page {canvas.getPageNumber()}"
    )

    canvas.restoreState()


class _ContractDocTemplate(BaseDocTemplate):
    def __init__(self, filename, contract_id, generated_at, **kwargs):
        self._contract_id = contract_id
        self._generated_at = generated_at
        super().__init__(filename, **kwargs)
        frame = Frame(
            MARGIN_H, 1.4 * cm,
            CONTENT_W, PAGE_H - 1.6 * cm - 1.4 * cm - 0.5 * cm,
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
            id="main"
        )
        template = PageTemplate(id="main_template", frames=[frame],
                                onPage=self._on_page)
        self.addPageTemplates([template])

    def _on_page(self, canvas, doc):
        _draw_page_decorations(canvas, doc, self._contract_id, self._generated_at)


def _format_currency(amount, currency):
    if amount is None:
        return "N/A"
    try:
        return f"{currency} {float(amount):,.2f}"
    except Exception:
        return str(amount)


def _parse_payment_schedule(raw) -> list:
    """Return a list of dicts from either a JSON string or an already-parsed list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def _payment_schedule_table(items: list, currency: str) -> Table:
    header_style = ParagraphStyle(
        "sched_hdr", fontName="Helvetica-Bold", fontSize=8.5,
        textColor=WHITE, leading=12
    )
    cell_style = ParagraphStyle(
        "sched_cell", fontName="Helvetica", fontSize=8.5,
        textColor=TEXT_DARK, leading=12
    )
    pct_style = ParagraphStyle(
        "sched_pct", fontName="Helvetica", fontSize=8.5,
        textColor=TEXT_DARK, leading=12, alignment=TA_RIGHT
    )

    col_w = [
        CONTENT_W * 0.26,  # Phase
        CONTENT_W * 0.32,  # Description
        CONTENT_W * 0.20,  # Amount
        CONTENT_W * 0.11,  # %
        CONTENT_W * 0.11,  # Due date
    ]

    rows = [[
        Paragraph("Phase",       header_style),
        Paragraph("Description", header_style),
        Paragraph("Amount",      header_style),
        Paragraph("%",           header_style),
        Paragraph("Due Date",    header_style),
    ]]

    for item in items:
        amount_str = _format_currency(item.get("amount"), currency) if item.get("amount") is not None else "-"
        pct_str    = f"{item['percentage']:.0f}%" if item.get("percentage") is not None else "-"
        due_str    = str(item.get("due_date") or "-")
        rows.append([
            Paragraph(item.get("phase", "-"),        cell_style),
            Paragraph(item.get("description") or "-", cell_style),
            Paragraph(amount_str,                      cell_style),
            Paragraph(pct_str,                         pct_style),
            Paragraph(due_str,                         cell_style),
        ])

    t = Table(rows, colWidths=col_w, hAlign="LEFT")
    row_count = len(rows)
    t.setStyle(TableStyle([
        # Header row
        ("BACKGROUND",   (0, 0), (-1, 0),       ACCENT),
        ("TEXTCOLOR",    (0, 0), (-1, 0),       WHITE),
        ("FONTNAME",     (0, 0), (-1, 0),       "Helvetica-Bold"),
        # Data rows, alternating
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),    [WHITE, colors.HexColor("#EEF4FB")]),
        # Shared
        ("GRID",         (0, 0), (-1, -1),      0.4, MID_GRAY),
        ("VALIGN",       (0, 0), (-1, -1),      "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1),      6),
        ("RIGHTPADDING", (0, 0), (-1, -1),      6),
        ("TOPPADDING",   (0, 0), (-1, -1),      5),
        ("BOTTOMPADDING",(0, 0), (-1, -1),      5),
        # Bottom total-row accent
        ("BACKGROUND",   (0, row_count - 1), (-1, row_count - 1), colors.HexColor("#D6E8F7")),
        ("FONTNAME",     (0, row_count - 1), (-1, row_count - 1), "Helvetica-Bold"),
    ]))
    return t


def _section_header(text, styles):
    return KeepTogether([
        Spacer(1, 0.4 * cm),
        Paragraph(text.upper(), styles["section_header"]),
        HRFlowable(width="100%", thickness=0.8, color=ACCENT, spaceAfter=6),
    ])


def _kv_table(rows, col_widths=None):
    """Render a list of (label, value) pairs as a two-column table."""
    if col_widths is None:
        col_widths = [5 * cm, CONTENT_W - 5 * cm]
    data = [[Paragraph(f"<b>{k}</b>", _label_style()), Paragraph(str(v), _value_style())]
            for k, v in rows]
    t = Table(data, colWidths=col_widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_GRAY),
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [WHITE, colors.HexColor("#FAFAFA")]),
        ("GRID", (0, 0), (-1, -1), 0.4, MID_GRAY),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
        ("TEXTCOLOR", (1, 0), (1, -1), TEXT_DARK),
    ]))
    return t


def _label_style():
    s = ParagraphStyle("kv_label",
                       fontName="Helvetica-Bold", fontSize=9,
                       textColor=NAVY, leading=13)
    return s


def _value_style():
    s = ParagraphStyle("kv_value",
                       fontName="Helvetica", fontSize=9,
                       textColor=TEXT_DARK, leading=13)
    return s


def _build_styles():
    base = getSampleStyleSheet()
    styles = {}

    styles["doc_title"] = ParagraphStyle(
        "doc_title",
        fontName="Helvetica-Bold", fontSize=22,
        textColor=NAVY, alignment=TA_CENTER,
        spaceAfter=4, leading=28
    )
    styles["doc_subtitle"] = ParagraphStyle(
        "doc_subtitle",
        fontName="Helvetica", fontSize=10,
        textColor=TEXT_MID, alignment=TA_CENTER,
        spaceAfter=2
    )
    styles["section_header"] = ParagraphStyle(
        "section_header",
        fontName="Helvetica-Bold", fontSize=10,
        textColor=ACCENT, spaceBefore=2, spaceAfter=2,
        leading=14, letterSpacing=0.8
    )
    styles["body"] = ParagraphStyle(
        "body",
        fontName="Helvetica", fontSize=9,
        textColor=TEXT_DARK, leading=14, spaceAfter=4,
        alignment=TA_JUSTIFY
    )
    styles["body_bold"] = ParagraphStyle(
        "body_bold",
        fontName="Helvetica-Bold", fontSize=9,
        textColor=TEXT_DARK, leading=14, spaceAfter=2
    )
    styles["note"] = ParagraphStyle(
        "note",
        fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=TEXT_MID, leading=13, spaceAfter=4,
        alignment=TA_JUSTIFY
    )
    styles["sig_name"] = ParagraphStyle(
        "sig_name",
        fontName="Helvetica-Bold", fontSize=9,
        textColor=TEXT_DARK, leading=13, spaceBefore=2
    )
    styles["sig_label"] = ParagraphStyle(
        "sig_label",
        fontName="Helvetica", fontSize=8,
        textColor=TEXT_MID, leading=12
    )
    return styles


def generate_contract_pdf(contract_context: dict, contract_terms: dict) -> bytes:
    buffer = io.BytesIO()
    contract_id = str(contract_context.get("contract_id", "N/A"))
    generated_at = str(contract_context.get("generated_at", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")))

    doc = _ContractDocTemplate(
        buffer,
        contract_id=contract_id,
        generated_at=generated_at,
        pagesize=A4,
        leftMargin=MARGIN_H, rightMargin=MARGIN_H,
        topMargin=MARGIN_V, bottomMargin=MARGIN_V,
    )

    styles = _build_styles()
    story = []

    # Title block
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        contract_context.get("contract_title", "Freelance Contract Agreement"),
        styles["doc_title"]
    ))
    story.append(Paragraph(
        f"Contract No. {contract_id} &nbsp;·&nbsp; Date: {generated_at}",
        styles["doc_subtitle"]
    ))
    story.append(Spacer(1, 0.2 * cm))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=8))

    # Preamble
    client_name     = contract_context.get("client",     {}).get("full_name", "N/A")
    freelancer_name = contract_context.get("freelancer", {}).get("full_name", "N/A")
    story.append(Paragraph(
        f"This Freelance Contract Agreement (<b>\"Agreement\"</b>) is entered into as of "
        f"<b>{contract_context.get('start_date', 'the start date')}</b> by and between "
        f"<b>{client_name}</b> (<b>\"Client\"</b>) and <b>{freelancer_name}</b> "
        f"(<b>\"Freelancer\"</b>).",
        styles["body"]
    ))

    # 1. Parties
    story.append(_section_header("1. Parties", styles))
    story.append(_kv_table([
        ("Client",     client_name),
        ("Freelancer", freelancer_name),
    ]))
    story.append(Spacer(1, 0.2 * cm))

    # 2. Project Scope
    story.append(_section_header("2. Project Scope", styles))
    job_title   = contract_context.get("job_post",  {}).get("job_title", "N/A")
    role_title  = contract_context.get("job_role",  {}).get("role_title", "N/A")
    scope       = contract_context.get("job_post",  {}).get("project_scope", "N/A")
    description = contract_context.get("job_post",  {}).get("job_description", "N/A")

    story.append(_kv_table([
        ("Job Title",      job_title),
        ("Role",           role_title),
        ("Project Scope",  scope),
    ]))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("<b>Description of Work</b>", styles["body_bold"]))
    story.append(Paragraph(description, styles["body"]))

    # 3. Financial Terms
    story.append(_section_header("3. Financial Terms", styles))
    currency = contract_context.get("budget_currency", "USD")
    payment_structure = contract_context.get("payment_structure", "N/A")
    fin_rows = [
        ("Agreed Budget",      _format_currency(contract_context.get("agreed_budget"), currency)),
        ("Payment Structure",  payment_structure.replace("_", " ").title() if payment_structure != "N/A" else "N/A"),
        ("Start Date",         str(contract_context.get("start_date", "N/A"))),
        ("End Date",           str(contract_context.get("end_date", "N/A"))),
        ("Agreed Duration",    str(contract_context.get("agreed_duration", "N/A"))),
    ]
    story.append(_kv_table(fin_rows))
    schedule_raw = contract_terms.get("payment_schedule")
    if schedule_raw:
        schedule_items = _parse_payment_schedule(schedule_raw)
        if schedule_items:
            story.append(Spacer(1, 0.2 * cm))
            story.append(Paragraph("<b>Payment Schedule</b>", styles["body_bold"]))
            story.append(_payment_schedule_table(schedule_items, currency))

    # 4. Legal Clauses
    story.append(_section_header("4. Legal Clauses", styles))
    dispute = contract_terms.get("dispute_resolution", "N/A")
    legal_rows = [
        ("Termination Notice",   f"{contract_terms.get('termination_notice', 'N/A')} days"),
        ("Governing Law",        contract_terms.get("governing_law", "N/A")),
        ("Confidentiality",      "Yes" if contract_terms.get("confidentiality") else "No"),
        ("Late Payment Penalty", f"{contract_terms.get('late_payment_penalty', 'N/A')}% per week"),
        ("Dispute Resolution",   dispute.replace("_", " ").title() if dispute != "N/A" else "N/A"),
        ("Revision Rounds",      str(contract_terms.get("revision_rounds", "N/A"))),
    ]
    story.append(_kv_table(legal_rows))

    if contract_terms.get("confidentiality") and contract_terms.get("confidentiality_text"):
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph("<b>Confidentiality Details</b>", styles["body_bold"]))
        story.append(Paragraph(contract_terms.get("confidentiality_text"), styles["body"]))

    if contract_terms.get("additional_clauses"):
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph("<b>Additional Clauses</b>", styles["body_bold"]))
        story.append(Paragraph(contract_terms.get("additional_clauses"), styles["body"]))

    # 5. General Provisions
    story.append(_section_header("5. General Provisions", styles))
    story.append(Paragraph(
        "<b>Entire Agreement.</b> This Agreement constitutes the entire agreement between "
        "the parties and supersedes all prior negotiations, representations, or agreements.",
        styles["body"]
    ))
    story.append(Paragraph(
        "<b>Amendments.</b> Any amendments to this Agreement must be made in writing and "
        "signed by both parties.",
        styles["body"]
    ))
    story.append(Paragraph(
        "<b>Severability.</b> If any provision of this Agreement is found to be "
        "unenforceable, the remaining provisions shall remain in full force and effect.",
        styles["body"]
    ))

    # 6. Signatures
    story.append(_section_header("6. Signatures", styles))
    story.append(Paragraph(
        "By signing below, the parties agree to the terms and conditions set forth in this Agreement.",
        styles["body"]
    ))
    story.append(Spacer(1, 0.5 * cm))

    sig_line = HRFlowable(width="85%", thickness=0.6, color=TEXT_DARK)
    half = (CONTENT_W - 1 * cm) / 2

    sig_data = [
        [
            [
                sig_line,
                Paragraph(client_name,    styles["sig_name"]),
                Paragraph("Client",       styles["sig_label"]),
                Spacer(1, 6),
                Paragraph("Date: _______________", styles["sig_label"]),
            ],
            [
                sig_line,
                Paragraph(freelancer_name, styles["sig_name"]),
                Paragraph("Freelancer",    styles["sig_label"]),
                Spacer(1, 6),
                Paragraph("Date: _______________", styles["sig_label"]),
            ],
        ]
    ]
    sig_table = Table(sig_data, colWidths=[half, half], hAlign="LEFT")
    sig_table.setStyle(TableStyle([
        ("VALIGN",  (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    story.append(sig_table)
    story.append(Spacer(1, 0.6 * cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MID_GRAY))
    story.append(Spacer(1, 0.15 * cm))
    story.append(Paragraph(
        "This document was generated electronically and is legally binding upon execution by both parties.",
        styles["note"]
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
