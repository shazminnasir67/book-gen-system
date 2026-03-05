"""
src/exporters/pdf_exporter.py
==============================
PDF export module for the Automated Book Generation System.

Uses `reportlab` to produce a publication-quality PDF with:
  - A styled title page
  - Chapter headings with decorative rules
  - Justified body text with proper line spacing
  - Page numbers in the footer

No business logic — this module is purely concerned with PDF rendering.
"""

import logging
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.colors import HexColor, black, white, grey
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    HRFlowable,
)
from reportlab.platypus.tableofcontents import TableOfContents

from src.config import Config
from src.database.supabase_client import Book, Chapter

logger = logging.getLogger(__name__)

# Brand colours
_PRIMARY = HexColor("#1A1A2E")
_ACCENT = HexColor("#E94560")
_BODY = HexColor("#2D2D2D")
_GREY = HexColor("#888888")


class PdfExporter:
    """
    Generates a styled PDF file from book and chapter data using reportlab.
    """

    def __init__(self, config: Config) -> None:
        """
        Initialise the PDF exporter.

        Args:
            config: Application configuration (for output path handling).
        """
        self._config = config

    def export(self, book: Book, chapters: list[Chapter], output_path: str) -> None:
        """
        Generate and save the PDF file.

        Args:
            book: Parent book record (title used on the title page).
            chapters: Ordered list of Chapter records with content.
            output_path: Absolute path where the .pdf will be written.

        Raises:
            RuntimeError: If the PDF cannot be rendered or saved.
        """
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            styles = self._build_styles()

            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=3 * cm,
                leftMargin=3 * cm,
                topMargin=3 * cm,
                bottomMargin=3 * cm,
                title=book.title,
                author="Automated Book Generation System",
            )

            story = []
            story += self._build_title_page(book.title, styles)
            story.append(PageBreak())

            for chapter in chapters:
                story += self._build_chapter(chapter, styles)
                story.append(PageBreak())

            doc.build(
                story,
                onFirstPage=self._first_page_footer,
                onLaterPages=self._later_pages_footer,
            )
            logger.info("PDF saved: %s", output_path)
        except Exception as exc:
            logger.error("PDF export failed for book='%s': %s", book.title, exc)
            raise RuntimeError(f"PDF export failed: {exc}") from exc

    # -----------------------------------------------------------------------
    # Style definitions
    # -----------------------------------------------------------------------

    def _build_styles(self) -> dict:
        """
        Build and return a dict of named ParagraphStyles for the document.

        Returns:
            dict: Mapping of style name → ParagraphStyle.
        """
        base = getSampleStyleSheet()
        return {
            "title": ParagraphStyle(
                "BookTitle",
                parent=base["Title"],
                fontSize=36,
                textColor=_PRIMARY,
                spaceAfter=12,
                spaceBefore=0,
                alignment=TA_CENTER,
                fontName="Helvetica-Bold",
                leading=44,
            ),
            "subtitle": ParagraphStyle(
                "BookSubtitle",
                fontSize=12,
                textColor=_GREY,
                alignment=TA_CENTER,
                fontName="Helvetica-Oblique",
                spaceAfter=6,
            ),
            "chapter_label": ParagraphStyle(
                "ChapterLabel",
                fontSize=11,
                textColor=_ACCENT,
                fontName="Helvetica-Bold",
                spaceBefore=0,
                spaceAfter=4,
                alignment=TA_LEFT,
            ),
            "chapter_title": ParagraphStyle(
                "ChapterTitle",
                fontSize=22,
                textColor=_PRIMARY,
                fontName="Helvetica-Bold",
                spaceBefore=4,
                spaceAfter=12,
                alignment=TA_LEFT,
                leading=28,
            ),
            "body": ParagraphStyle(
                "BookBody",
                fontSize=11,
                textColor=_BODY,
                fontName="Times-Roman",
                alignment=TA_JUSTIFY,
                leading=18,
                spaceAfter=8,
                firstLineIndent=24,
            ),
        }

    # -----------------------------------------------------------------------
    # Story builders
    # -----------------------------------------------------------------------

    def _build_title_page(self, title: str, styles: dict) -> list:
        """
        Construct flowable elements for the title page.

        Args:
            title: Book title.
            styles: Style dictionary from _build_styles().

        Returns:
            List of reportlab Flowable objects.
        """
        elements = [
            Spacer(1, 6 * cm),
            Paragraph(title.upper(), styles["title"]),
            Spacer(1, 0.5 * cm),
            HRFlowable(width="60%", thickness=2, color=_ACCENT, spaceAfter=12),
            Spacer(1, 0.3 * cm),
            Paragraph("Generated by Automated Book Generation System", styles["subtitle"]),
        ]
        return elements

    def _build_chapter(self, chapter: Chapter, styles: dict) -> list:
        """
        Construct flowable elements for a single chapter.

        Args:
            chapter: Chapter record with number, title, and content.
            styles: Style dictionary from _build_styles().

        Returns:
            List of reportlab Flowable objects.
        """
        elements = [
            Paragraph(f"CHAPTER {chapter.chapter_number}", styles["chapter_label"]),
        ]

        chapter_title = chapter.title or f"Chapter {chapter.chapter_number}"
        elements.append(Paragraph(chapter_title, styles["chapter_title"]))
        elements.append(HRFlowable(width="100%", thickness=1, color=_PRIMARY, spaceAfter=16))
        elements.append(Spacer(1, 0.4 * cm))

        content = chapter.content or "(No content generated)"
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        for para_text in paragraphs:
            # Escape XML special characters for reportlab
            safe_text = (
                para_text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            elements.append(Paragraph(safe_text, styles["body"]))

        return elements

    # -----------------------------------------------------------------------
    # Page decorators (footer / header)
    # -----------------------------------------------------------------------

    def _first_page_footer(self, canvas, doc) -> None:
        """Draw the footer on the first (title) page — intentionally blank."""
        pass

    def _later_pages_footer(self, canvas, doc) -> None:
        """
        Draw a page number footer on all pages after the title page.

        Args:
            canvas: ReportLab canvas object.
            doc: The document template.
        """
        canvas.saveState()
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(_GREY)
        page_num_text = f"— {doc.page} —"
        canvas.drawCentredString(A4[0] / 2, 1.5 * cm, page_num_text)
        canvas.restoreState()
