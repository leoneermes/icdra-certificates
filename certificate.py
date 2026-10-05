"""Generazione degli attestati di partecipazione ICDRA 2026 (PDF)."""
import io
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Frame, Paragraph

FONT_DIR = Path(__file__).parent / "fonts"


def _register_fonts():
    if "Cal" in pdfmetrics.getRegisteredFontNames():
        return
    for name, file in [
        ("Cal", "Caladea-Regular.ttf"), ("Cal-B", "Caladea-Bold.ttf"), ("Cal-I", "Caladea-Italic.ttf"),
        ("DJ", "DejaVuSerif.ttf"), ("DJ-B", "DejaVuSerif-Bold.ttf"), ("DJ-I", "DejaVuSerif-Italic.ttf"),
    ]:
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
    for f in ("Cal", "DJ"):
        registerFontFamily(f, normal=f, bold=f"{f}-B", italic=f"{f}-I", boldItalic=f"{f}-B")


_register_fonts()
_CAL_GLYPHS = pdfmetrics.getFont("Cal").face.charToGlyph


def _family(*texts):
    """Caladea (metrica Cambria) se copre tutti i caratteri, altrimenti DejaVu Serif (es. vietnamita)."""
    for t in texts:
        for ch in t or "":
            if not ch.isspace() and ord(ch) not in _CAL_GLYPHS:
                return "DJ"
    return "Cal"


def role_sentence(cfg, row):
    """Frase specifica per ruolo: orale/invited/keynote, poster, chair."""
    custom = (row.get("custom") or "").strip()
    if custom:
        return ", and " + escape(custom)
    ph = cfg.get("role_phrases") or {}
    parts = []
    talk = row.get("talk") or ""
    if talk and ph.get(talk):
        session = escape((row.get("session") or "").strip())
        t = ph[talk].format(session=session)
        if not session:  # nessuna sessione: tolgo il riferimento
            t = t.split(" in the ")[0]
        parts.append(t)
    if row.get("poster") and ph.get("poster"):
        parts.append(ph["poster"])
    if row.get("chair") and ph.get("chair"):
        parts.append(ph["chair"])
    if not parts:
        return ""
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return ", and " + joined


def build_body(cfg, row):
    """Compone il testo del corpo sostituendo i segnaposto."""
    hours = (cfg.get("hours") or "").strip()
    hours_sentence = cfg["hours_sentence"].format(hours=hours) if hours else ""
    title = (row.get("contribution") or "").strip()
    contribution_sentence = (
        cfg["contribution_sentence"].format(title=escape(title)) if title and cfg.get("include_contribution") else ""
    )
    return cfg["body"].format(
        hours_sentence=hours_sentence,
        role_sentence=role_sentence(cfg, row),
        contribution_sentence=contribution_sentence,
    )


def make_certificate(row, cfg, template_pdf=None, logo=None, signature=None):
    """Restituisce i byte del PDF per un partecipante.

    row: dict con name, affiliation, contribution
    cfg: impostazioni testuali e grafiche
    template_pdf / logo / signature: bytes opzionali
    """
    name = row["name"].strip()
    affiliation = (row.get("affiliation") or "").strip()
    body = build_body(cfg, row)
    fam = _family(name, affiliation, body)
    green = HexColor(cfg.get("color", "#2E6B3A"))

    if template_pdf:
        tpl_page = PdfReader(io.BytesIO(template_pdf)).pages[0]
        W, H = float(tpl_page.mediabox.width), float(tpl_page.mediabox.height)
    else:
        W, H = landscape(A4)

    top = cfg.get("margin_top_mm", 25) * mm
    bottom = cfg.get("margin_bottom_mm", 25) * mm
    side = cfg.get("margin_side_mm", 30) * mm

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.setTitle(f"Certificate of attendance – {name}")
    c.setAuthor(cfg.get("signatory_name", ""))

    if not template_pdf:
        c.setStrokeColor(green)
        c.setLineWidth(2.2)
        c.rect(12 * mm, 12 * mm, W - 24 * mm, H - 24 * mm)
        c.setLineWidth(0.6)
        c.rect(15 * mm, 15 * mm, W - 30 * mm, H - 30 * mm)

    y = H - top
    if logo:
        img = ImageReader(io.BytesIO(logo))
        iw, ih = img.getSize()
        lh = cfg.get("logo_height_mm", 22) * mm
        lw = lh * iw / ih
        c.drawImage(img, (W - lw) / 2, y - lh, lw, lh, mask="auto")
        y -= lh + 8 * mm

    c.setFillColor(green)
    c.setFont(f"{fam}-B", 26)
    c.drawCentredString(W / 2, y - 26, cfg["title"].upper())
    y -= 26 + 12 * mm

    c.setFillColor(HexColor("#222222"))
    c.setFont(f"{fam}-I", 13)
    c.drawCentredString(W / 2, y - 13, cfg["intro"])
    y -= 13 + 9 * mm

    # Nome: riduce il corpo se troppo lungo
    size = 24
    while pdfmetrics.stringWidth(name, f"{fam}-B", size) > W - 2 * side and size > 14:
        size -= 1
    c.setFillColor(green)
    c.setFont(f"{fam}-B", size)
    c.drawCentredString(W / 2, y - size, name)
    y -= size + 4 * mm

    if affiliation:
        c.setFillColor(HexColor("#444444"))
        aff_style = ParagraphStyle("aff", fontName=f"{fam}-I", fontSize=12, leading=15, alignment=TA_CENTER)
        p = Paragraph(escape(affiliation), aff_style)
        _, ph = p.wrap(W - 2 * side, 100)
        p.drawOn(c, side, y - ph)
        y -= ph + 7 * mm
    else:
        y -= 4 * mm

    body_style = ParagraphStyle(
        "body", fontName=fam, fontSize=13, leading=19, textColor=HexColor("#222222"),
        alignment=TA_CENTER if cfg.get("center_body", True) else TA_JUSTIFY,
    )
    p = Paragraph(body, body_style)
    _, ph = p.wrap(W - 2 * side, H)
    p.drawOn(c, side, y - ph)
    y_body_end = y - ph

    # Luogo e data (sinistra), firma (destra)
    sig_block_top = bottom + 38 * mm
    c.setFillColor(HexColor("#222222"))
    c.setFont(fam, 11.5)
    c.drawString(side, bottom + 12 * mm, cfg["place_date"])

    sx = W - side - 75 * mm
    if signature:
        img = ImageReader(io.BytesIO(signature))
        iw, ih = img.getSize()
        sh = 18 * mm
        sw = min(sh * iw / ih, 70 * mm)
        sh = sw * ih / iw
        c.drawImage(img, sx + (75 * mm - sw) / 2, bottom + 15 * mm, sw, sh, mask="auto")
    c.setStrokeColor(HexColor("#888888"))
    c.setLineWidth(0.5)
    c.line(sx, bottom + 14 * mm, sx + 75 * mm, bottom + 14 * mm)
    c.setFont(f"{fam}-B", 11)
    c.drawCentredString(sx + 37.5 * mm, bottom + 9 * mm, cfg["signatory_name"])
    c.setFont(f"{fam}-I", 10)
    for i, line in enumerate(cfg["signatory_role"].split("\n")):
        c.drawCentredString(sx + 37.5 * mm, bottom + 4.5 * mm - i * 4.5 * mm, line)

    overflow = y_body_end < sig_block_top
    c.showPage()
    c.save()
    pdf = buf.getvalue()

    if template_pdf:
        tpl = PdfReader(io.BytesIO(template_pdf)).pages[0]
        tpl.merge_page(PdfReader(io.BytesIO(pdf)).pages[0])
        out = PdfWriter()
        out.add_page(tpl)
        out.add_metadata({"/Title": f"Certificate of attendance – {name}"})
        b = io.BytesIO()
        out.write(b)
        pdf = b.getvalue()

    return pdf, overflow


def render_preview(pdf_bytes, scale=1.4):
    """PNG di anteprima della prima pagina."""
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=scale).to_pil()
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()
