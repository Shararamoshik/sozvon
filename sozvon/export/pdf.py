"""A4 PDF with embedded local Cyrillic fonts and literal escaped paragraphs."""

from pathlib import Path
from typing import BinaryIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate

from sozvon.export.document import ExportDocument


def render_pdf(document: ExportDocument, target: Path | BinaryIO, fonts: Path) -> None:
    regular, bold = "SozvonGolos", "SozvonGolosBold"
    coverage = {}
    for name, filename in ((regular, "GolosText-Regular.ttf"),
                           (bold, "GolosText-SemiBold.ttf")):
        path = fonts / filename
        if not path.is_file():
            raise ValueError(f"В сборке отсутствует шрифт для PDF: {filename}")
        # Open ourselves: ReportLab's filename loader can fall back to URL fetching.
        with path.open("rb") as source:
            font = TTFont(name, source)
        pdfmetrics.registerFont(font)
        coverage[name] = font.face.charToGlyph
    styles = {
        "title": ParagraphStyle("Title", fontName=bold, fontSize=18, leading=23,
                                spaceAfter=16, splitLongWords=1),
        "h2": ParagraphStyle("Section", fontName=bold, fontSize=13, leading=17,
                             spaceBefore=12, spaceAfter=7, keepWithNext=True, splitLongWords=1),
        "p": ParagraphStyle("Body", fontName=regular, fontSize=11, leading=15,
                            spaceAfter=7, alignment=TA_LEFT, splitLongWords=1),
        "meta": ParagraphStyle("Meta", fontName=regular, fontSize=9, leading=12,
                               textColor=colors.HexColor("#5F5A50"), spaceAfter=5, splitLongWords=1),
        "quote": ParagraphStyle("Quote", fontName=regular, fontSize=10, leading=14,
                                leftIndent=12, spaceAfter=9, splitLongWords=1),
    }

    def paragraph(value, kind):
        glyphs = coverage[styles[kind].fontName]
        for char in value:
            # Only these layout controls need no glyph. Unicode spaces and
            # invisible characters must still be covered by the selected font.
            if char not in "\r\n\t" and not glyphs.get(ord(char)):
                raise ValueError(
                    f"Шрифт PDF не поддерживает символ U+{ord(char):04X}. "
                    "Выберите экспорт в DOCX для сохранения исходного текста."
                )
        safe = escape(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
        return Paragraph(safe, styles[kind])

    story = [paragraph(document.title, "title")]
    for block in document.blocks:
        if block.kind not in {"h2", "p", "meta", "quote"}:
            raise ValueError("Неизвестный блок документа")
        story.append(paragraph(block.text, block.kind))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 9)
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, str(doc.page))
        canvas.restoreState()

    pdf = SimpleDocTemplate(str(target) if isinstance(target, Path) else target, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm,
                            title=document.title, author="Созвон")
    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
