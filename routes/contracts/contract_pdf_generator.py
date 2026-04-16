import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib import colors


def _format_currency(amount, currency):
    if amount is None:
        return "N/A"
    try:
        amount_value = float(amount)
        return f"{currency} {amount_value:,.2f}"
    except Exception:
        return str(amount)


def generate_contract_pdf(contract_context: dict, contract_terms: dict) -> bytes:
    """Create a PDF document from contract context and contract terms."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title", parent=styles["Heading1"], alignment=TA_CENTER, spaceAfter=18)
    header_style = ParagraphStyle("Header", parent=styles["Heading2"], alignment=TA_LEFT, spaceBefore=12, spaceAfter=8)
    normal = styles["BodyText"]

    story = []
    story.append(Paragraph(contract_context.get("contract_title", "Contract Agreement"), title_style))
    story.append(Paragraph(f"Contract ID: {contract_context.get('contract_id')}", normal))
    story.append(Paragraph(f"Generated on: {contract_context.get('generated_at')}", normal))
    story.append(Spacer(1, 16))

    story.append(Paragraph("Parties", header_style))
    story.append(Paragraph(f"Client: {contract_context['client'].get('full_name', 'N/A')}", normal))
    story.append(Paragraph(f"Freelancer: {contract_context['freelancer'].get('full_name', 'N/A')}", normal))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Project Scope", header_style))
    story.append(Paragraph(f"Job Title: {contract_context['job_post'].get('job_title', 'N/A')}", normal))
    story.append(Paragraph(f"Role Title: {contract_context['job_role'].get('role_title', 'N/A')}", normal))
    story.append(Paragraph(f"Project Scope: {contract_context['job_post'].get('project_scope', 'N/A')}", normal))
    story.append(Paragraph("Job Description:", normal))
    story.append(Paragraph(contract_context['job_post'].get('job_description', 'N/A'), normal))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Financial Terms", header_style))
    story.append(Paragraph(f"Budget: {_format_currency(contract_context.get('agreed_budget'), contract_context.get('budget_currency', 'USD'))}", normal))
    story.append(Paragraph(f"Payment Structure: {contract_context.get('payment_structure', 'N/A')}", normal))
    story.append(Paragraph(f"Start Date: {contract_context.get('start_date', 'N/A')}", normal))
    story.append(Paragraph(f"End Date: {contract_context.get('end_date', 'N/A')}", normal))
    story.append(Paragraph(f"Agreed Duration: {contract_context.get('agreed_duration', 'N/A')}", normal))
    if contract_terms.get("payment_schedule"):
        story.append(Spacer(1, 6))
        story.append(Paragraph("Payment Schedule:", normal))
        story.append(Paragraph(contract_terms.get("payment_schedule"), normal))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Legal Clauses", header_style))
    story.append(Paragraph(f"Termination Notice: {contract_terms.get('termination_notice', 'N/A')} days", normal))
    story.append(Paragraph(f"Governing Law: {contract_terms.get('governing_law', 'N/A')}", normal))
    story.append(Paragraph(f"Confidentiality: {'Yes' if contract_terms.get('confidentiality') else 'No'}", normal))
    if contract_terms.get('confidentiality') and contract_terms.get('confidentiality_text'):
        story.append(Paragraph("Confidentiality Details:", normal))
        story.append(Paragraph(contract_terms.get('confidentiality_text'), normal))
    story.append(Paragraph(f"Late Payment Penalty: {contract_terms.get('late_payment_penalty', 'N/A')}% per week", normal))
    story.append(Paragraph(f"Dispute Resolution: {contract_terms.get('dispute_resolution', 'N/A')}", normal))
    story.append(Paragraph(f"Revision Rounds: {contract_terms.get('revision_rounds', 'N/A')}", normal))
    if contract_terms.get('additional_clauses'):
        story.append(Paragraph("Additional Clauses:", normal))
        story.append(Paragraph(contract_terms.get('additional_clauses'), normal))
    story.append(Spacer(1, 18))

    story.append(Paragraph("Signatures", header_style))
    story.append(Paragraph("Client: _________________________", normal))
    story.append(Paragraph("Freelancer: _________________________", normal))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
