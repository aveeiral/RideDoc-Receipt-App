"""Builds the RideDoc invoice PDF (A4) and renders previews."""
from __future__ import annotations

from fpdf import FPDF, XPos, YPos
from fpdf.fonts import FontFace

from . import config
from .billing import GST_RATE, Bill, amount_in_words, format_inr, format_rate

PAGE_W, PAGE_H = 210, 297
MARGIN = 12
CONTENT_W = PAGE_W - 2 * MARGIN
BRAND = (31, 92, 153)        # same blue as the rate card
LIGHT = (232, 238, 246)
FOOTER_SPACE = 44            # mm reserved at the bottom of the last page


def _t(value) -> str:
    """Core PDF fonts are Latin-1 only; replace anything else instead of crashing."""
    return str(value if value not in (None, "") else "-").encode("latin-1", "replace").decode("latin-1")


class _InvoicePDF(FPDF):
    def header(self):
        self.set_draw_color(0)
        self.set_line_width(0.4)
        self.rect(7, 7, PAGE_W - 14, PAGE_H - 14)

    def footer(self):
        self.set_y(-13)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(120)
        self.cell(0, 4, f"Page {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0)


def _letterhead(pdf: FPDF) -> None:
    top = 11
    bottoms = []

    # Left: logo
    logo = config.logo_path()
    left_edge = MARGIN
    if logo:
        pdf.image(str(logo), x=MARGIN + 1, y=top + 3, w=42, h=24, keep_aspect_ratio=True)
        left_edge = MARGIN + 45
        bottoms.append(top + 3 + 24)

    # Right: payment QR code with caption
    qr = config.qr_path()
    right_edge = PAGE_W - MARGIN
    if qr:
        qr_size = 27
        qr_x = PAGE_W - MARGIN - qr_size - 1
        pdf.image(str(qr), x=qr_x, y=top, w=qr_size, h=qr_size, keep_aspect_ratio=True)
        pdf.set_xy(qr_x - 3, top + qr_size + 0.5)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(qr_size + 6, 4, _t(config.QR_CAPTION), align="C")
        right_edge = qr_x - 3
        bottoms.append(top + qr_size + 4.5)

    # Centre: "TAX INVOICE" + name + contact lines, centred between logo and QR
    text_x = left_edge
    text_w = right_edge - left_edge

    pdf.set_xy(text_x, top)
    label_w = 36
    pdf.set_x(text_x + (text_w - label_w) / 2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(0)
    pdf.set_text_color(255)
    pdf.cell(label_w, 6, "TAX INVOICE", align="C", fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0)

    pdf.set_x(text_x)
    pdf.set_font("Helvetica", "B", 34)
    pdf.cell(text_w, 14, _t(config.BUSINESS_NAME), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(text_x)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(text_w, 5, _t(config.BUSINESS_CONTACT_LINE), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(text_x)
    pdf.cell(text_w, 5, _t(config.BUSINESS_WEB_LINE), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    y = max([pdf.get_y()] + bottoms) + 3
    pdf.set_line_width(0.4)
    pdf.line(7, y, PAGE_W - 7, y)
    pdf.set_y(y + 4)


def _detail_block(pdf: FPDF, x: float, y: float, w: float, title: str, rows) -> float:
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_fill_color(*LIGHT)
    pdf.cell(w, 6.5, f"  {title}", fill=True)
    cy = y + 8
    label_w = 19
    for label, value in rows:
        pdf.set_xy(x + 2, cy)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.cell(label_w, 5, label)
        pdf.set_xy(x + 2 + label_w, cy)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.multi_cell(w - label_w - 4, 5, _t(value), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        cy = pdf.get_y()
    return cy


def _details(pdf: FPDF, bill: Bill) -> None:
    y = pdf.get_y()
    gap = 3
    w = (CONTENT_W - 2 * gap) / 3
    blocks = [
        ("Invoice Details", [("Invoice No", bill.invoice_no),
                             ("Date", bill.bill_date.strftime("%d-%m-%Y")),
                             ("Time", bill.bill_time),
                             ("Payment", bill.payment_mode)]),
        ("Customer Details", [("Name", bill.customer_name),
                              ("Phone", bill.phone)]),
        ("Vehicle Details", [("Model", bill.vehicle_model),
                             ("Reg. No.", bill.reg_no),
                             ("Mileage", f"{bill.mileage} km" if bill.mileage else ""),
                             ("Segment", bill.segment)]),
    ]
    bottoms = [_detail_block(pdf, MARGIN + i * (w + gap), y, w, t, rows) for i, (t, rows) in enumerate(blocks)]
    bottom = max(bottoms) + 1.5
    pdf.set_line_width(0.25)
    for i in range(3):
        pdf.rect(MARGIN + i * (w + gap), y, w, bottom - y)
    pdf.set_y(bottom + 6)


def _items_table(pdf: FPDF, bill: Bill) -> None:
    pdf.set_font("Helvetica", "", 9)
    pdf.set_draw_color(150)
    pdf.set_line_width(0.2)
    with pdf.table(
        width=CONTENT_W,
        col_widths=(13, 73, 26, 14, 28, 32),
        text_align=("CENTER", "LEFT", "RIGHT", "CENTER", "RIGHT", "RIGHT"),
        line_height=7,
        headings_style=FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=BRAND),
        cell_fill_color=(246, 248, 251),
        cell_fill_mode="ROWS",
        padding=(0, 2),
    ) as table:
        head = table.row()
        gst_head = f"GST @ {format_rate(GST_RATE)}"
        for h in ("S.No", "Service Description", "Rate (Rs.)", "Qty", gst_head, "Amount (Rs.)"):
            head.cell(h)
        for n, item in enumerate(bill.items, start=1):
            row = table.row()
            row.cell(str(n))
            row.cell(_t(item.service))
            row.cell(format_inr(item.rate))
            row.cell(str(item.qty))
            row.cell(format_inr(item.gst))
            row.cell(format_inr(item.total))
    pdf.set_draw_color(0)


def _totals(pdf: FPDF, bill: Bill) -> None:
    t = bill.totals
    rows = [
        ("Sub Total (incl. GST)", format_inr(t.gross_total)),
    ]
    if t.discount > 0:
        rows.append((f"Coupon ({t.coupon_code})" if t.coupon_code else "Discount", f"- {format_inr(t.discount)}"))

    needed = 7 * (len(rows) + 1) + 18
    if pdf.get_y() + needed > PAGE_H - 18:
        pdf.add_page()
    pdf.ln(3)

    label_w, value_w = 58, 36
    x = PAGE_W - MARGIN - label_w - value_w
    pdf.set_draw_color(170)
    pdf.set_line_width(0.2)
    for label, value in rows:
        pdf.set_x(x)
        pdf.set_font("Helvetica", "B" if label.startswith("Sub Total") else "", 9.5)
        pdf.cell(label_w, 7, label, border="B")
        pdf.cell(value_w, 7, value, border="B", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(x)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(*BRAND)
    pdf.set_text_color(255)
    pdf.cell(label_w, 9, "  GRAND TOTAL (Rs.)", fill=True)
    pdf.cell(value_w, 9, format_inr(t.grand_total) + " ", fill=True, align="R",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0)
    pdf.set_draw_color(0)

    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(32, 5, "Amount in words:")
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(CONTENT_W - 32, 5, amount_in_words(t.grand_total), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _closing(pdf: FPDF) -> None:
    if pdf.get_y() > PAGE_H - 18 - FOOTER_SPACE:
        pdf.add_page()
    y = PAGE_H - 18 - FOOTER_SPACE + 4
    pdf.set_line_width(0.3)
    pdf.line(7, y - 3, PAGE_W - 7, y - 3)

    # Left: thank-you note
    pdf.set_xy(MARGIN, y + 2)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*BRAND)
    pdf.cell(110, 7, "Thank you for choosing RideDoc!", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.cell(110, 5, "We look forward to serving you again.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(110)
    pdf.cell(110, 5, "This is a computer-generated invoice.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0)

    # Right: authorised signature
    sig_w = 60
    sig_x = PAGE_W - MARGIN - sig_w
    pdf.set_xy(sig_x, y + 2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(sig_w, 5, f"For {config.BUSINESS_NAME}", align="C")
    line_y = y + 28
    pdf.line(sig_x + 4, line_y, sig_x + sig_w - 4, line_y)
    pdf.set_xy(sig_x, line_y + 1)
    pdf.cell(sig_w, 5, "Authorised Signatory", align="C")


def build_invoice_pdf(bill: Bill) -> bytes:
    pdf = _InvoicePDF(format="A4", unit="mm")
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_title(f"{config.BUSINESS_NAME} Invoice {bill.invoice_no}")
    pdf.set_author(config.BUSINESS_NAME)
    pdf.add_page()
    _letterhead(pdf)
    _details(pdf, bill)
    _items_table(pdf, bill)
    _totals(pdf, bill)
    _closing(pdf)
    return bytes(pdf.output())


def render_pdf_images(pdf_bytes: bytes, scale: float = 2.0):
    """Render each PDF page to a PIL image (an exact preview of the download)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        return [doc[i].render(scale=scale).to_pil() for i in range(len(doc))]
    finally:
        doc.close()
