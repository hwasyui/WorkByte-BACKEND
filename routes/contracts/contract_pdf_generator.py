import io
import json
import re
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


# The contract form writes one line per milestone, e.g.
#   Milestone 1: Wireframes approved - 30% payment (paid after client approval)
# with the percentage and the note both optional. Mirrors the pattern the form
# itself uses to read the value back, so the two stay in step.
_MILESTONE_LINE_RE = re.compile(
    r"^Milestone\s+\d+\s*:\s*(?P<title>.+?)"
    r"(?:\s*-\s*(?P<percentage>[\d.]+)%\s*payment)?"
    r"(?:\s*\((?P<note>.*)\))?$"
)


def _parse_payment_schedule(raw, payment_structure=None, agreed_budget=None):
    """Turn a stored payment_schedule into (table rows, free text).

    Three shapes reach this function:
      * a list, or a JSON string holding one - the richest form, carrying an
        explicit amount and due_date per phase;
      * the milestone lines a milestone_based contract is saved as, which is what
        the app actually sends;
      * a single sentence for a full_payment contract, like "100% upfront".

    Only the first two become a table. The last is returned as free text, because
    one line spread across a five-column grid reads as a rendering fault. Exactly
    one of the two return values is ever populated.
    """
    if isinstance(raw, list):
        return raw, None
    if not isinstance(raw, str):
        return [], None

    text = raw.strip()
    if not text:
        return [], None

    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed, None
        except (json.JSONDecodeError, ValueError):
            pass

    # A full_payment arrangement is prose, not a schedule of phases.
    if payment_structure != "milestone_based":
        return [], text

    items = []
    for index, line in enumerate(l.strip() for l in text.splitlines()):
        if not line:
            continue
        match = _MILESTONE_LINE_RE.match(line)
        if not match:
            # Keep the line rather than drop it: a milestone the form could not
            # round-trip is still a term of the agreement.
            items.append({"phase": f"Milestone {index + 1}", "description": line})
            continue

        percentage = None
        if match.group("percentage"):
            try:
                percentage = float(match.group("percentage"))
            except ValueError:
                percentage = None

        # The form collects a percentage but never an amount, so derive it. Both
        # the budget and the split are fixed by this point, so this is arithmetic
        # on agreed terms rather than an assumption about them.
        amount = None
        if percentage is not None and agreed_budget is not None:
            try:
                amount = float(agreed_budget) * percentage / 100.0
            except (TypeError, ValueError):
                amount = None

        description = match.group("title").strip()
        note = (match.group("note") or "").strip()
        if note:
            description = f"{description} ({note})"

        items.append({
            "phase": f"Milestone {len(items) + 1}",
            "description": description,
            "percentage": percentage,
            "amount": amount,
        })

    return items, None


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

    # Only carry the columns this schedule actually has. The app collects a
    # percentage and no due date, so a fixed five-column grid left two of them
    # showing nothing but dashes.
    show_amount = any(item.get("amount") is not None for item in items)
    show_pct    = any(item.get("percentage") is not None for item in items)
    show_due    = any(item.get("due_date") for item in items)

    columns = [("Phase", 0.24), ("Description", 0.38)]
    if show_amount:
        columns.append(("Amount", 0.20))
    if show_pct:
        columns.append(("%", 0.10))
    if show_due:
        columns.append(("Due Date", 0.16))

    weight_total = sum(weight for _, weight in columns)
    col_w = [CONTENT_W * (weight / weight_total) for _, weight in columns]

    rows = [[Paragraph(label, header_style) for label, _ in columns]]

    for item in items:
        row = [
            Paragraph(item.get("phase") or "-", cell_style),
            Paragraph(item.get("description") or "-", cell_style),
        ]
        if show_amount:
            amount = item.get("amount")
            row.append(Paragraph(_format_currency(amount, currency) if amount is not None else "-", cell_style))
        if show_pct:
            pct = item.get("percentage")
            row.append(Paragraph(f"{pct:.0f}%" if pct is not None else "-", pct_style))
        if show_due:
            row.append(Paragraph(str(item.get("due_date") or "-"), cell_style))
        rows.append(row)

    # A real total, so the emphasised bottom row means something. Without it the
    # styling below simply bolded the last milestone, which read as a total that
    # happened to be wrong.
    total_pct = sum(i["percentage"] for i in items if i.get("percentage") is not None)
    total_amount = sum(i["amount"] for i in items if i.get("amount") is not None)
    has_total = show_pct or show_amount
    if has_total:
        total_row = [Paragraph("Total", cell_style), Paragraph("", cell_style)]
        if show_amount:
            total_row.append(Paragraph(_format_currency(total_amount, currency), cell_style))
        if show_pct:
            total_row.append(Paragraph(f"{total_pct:.0f}%", pct_style))
        if show_due:
            total_row.append(Paragraph("", cell_style))
        rows.append(total_row)

    t = Table(rows, colWidths=col_w, hAlign="LEFT")
    style = [
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
    ]
    if has_total:
        last = len(rows) - 1
        style += [
            ("BACKGROUND", (0, last), (-1, last), colors.HexColor("#D6E8F7")),
            ("FONTNAME",   (0, last), (-1, last), "Helvetica-Bold"),
        ]
    t.setStyle(TableStyle(style))
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
    # The contract's role_title wins; the job role is only the fallback for older
    # rows that never had one set.
    role_title  = contract_context.get("role_title") or contract_context.get("job_role", {}).get("role_title", "N/A")
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
    schedule_items, schedule_text = _parse_payment_schedule(
        contract_terms.get("payment_schedule"),
        payment_structure=payment_structure,
        agreed_budget=contract_context.get("agreed_budget"),
    )
    if schedule_items or schedule_text:
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph("<b>Payment Schedule</b>", styles["body_bold"]))
        if schedule_items:
            story.append(_payment_schedule_table(schedule_items, currency))
        else:
            story.append(Paragraph(schedule_text, styles["body"]))

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
