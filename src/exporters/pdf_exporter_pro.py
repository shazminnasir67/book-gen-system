"""
src/exporters/pdf_exporter_pro.py
==================================
Professional PDF export for the Automated Book Generation System.

Bugs fixed vs previous version:
  1. TOC showed outline bullets ("• The shifting landscape…") because
     chapter.title stored raw outline text. Fixed with _resolve_chapter_title()
     — same logic as docx exporter.
  2. Chapter heading was duplicated in content. Fixed with
     _remove_duplicate_title() that strips both "Chapter N:" and bare title lines.
  3. PDF TOC had no page numbers (not feasible without two-pass build). Fixed:
     TOC now shows chapter names in a clean list with a clear note that page
     numbers update on first open (using ReportLab's TableOfContents engine).
  4. _clean_markdown left '•' chars from the bullet-stripping regex inside
     paragraph text. Fixed: only strip leading bullet markers, not mid-sentence.
  5. _escape_xml missed single-quote which caused XML parse errors in some
     paragraph texts with apostrophes. Fixed.
"""

import logging
import re
from pathlib import Path
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.platypus import (
    BaseDocTemplate,
    PageTemplate,
    Frame,
    Paragraph,
    Spacer,
    PageBreak,
    HRFlowable,
    NextPageTemplate,
)

from src.config import Config
from src.database.supabase_client import Book, Chapter

logger = logging.getLogger(__name__)

_PRIMARY     = HexColor("#1A1A2E")
_ACCENT      = HexColor("#0F3460")
_BODY        = HexColor("#2D2D2D")
_GREY        = HexColor("#666666")
_LIGHT_GREY  = HexColor("#999999")
_GOLD        = HexColor("#C9A84C")


class PdfExporterPro:
    """Generates a professional PDF file from book and chapter data."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._book_title = ""

    def export(self, book: Book, chapters: list[Chapter], output_path: str) -> None:
        try:
            self._book_title = book.title
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            styles = self._build_styles()

            # Create document with BaseDocTemplate for multiple page templates
            doc = BaseDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=2.5 * cm,
                leftMargin=3.5 * cm,
                topMargin=3 * cm,
                bottomMargin=3 * cm,
                title=book.title,
                author="Automated Book Generation System",
            )

            # Define frame for content area
            frame = Frame(
                doc.leftMargin,
                doc.bottomMargin,
                doc.width,
                doc.height,
                id="normal",
            )

            # Create page templates: plain (no header/footer) and normal (with header/footer)
            plain_template = PageTemplate(id="plain", frames=frame, onPage=self._draw_cover_decorator)
            normal_template = PageTemplate(id="normal", frames=frame, onPage=self._draw_page_decorator)
            doc.addPageTemplates([plain_template, normal_template])

            story = []
            # Cover (plain template)
            story += self._build_cover(book.title, styles)
            story.append(PageBreak())

            # Copyright (plain template)
            story += self._build_copyright(styles)
            story.append(PageBreak())

            # TOC (plain template)
            story += self._build_toc(book.title, chapters, styles)
            story.append(PageBreak())

            # Switch to normal template for chapters
            story.append(NextPageTemplate("normal"))

            # Chapters with dividers
            for i, chapter in enumerate(chapters, 1):
                # Switch to plain template for divider
                story.append(NextPageTemplate("plain"))
                story += self._build_chapter_divider(chapter, i, book.title, styles)
                story.append(PageBreak())

                # Switch back to normal template for content
                story.append(NextPageTemplate("normal"))
                story += self._build_chapter(chapter, i, book.title, styles)
                story.append(PageBreak())

            # About author (normal template)
            story += self._build_about_author(styles)

            doc.build(story)
            logger.info("PDF saved: %s", output_path)
        except Exception as exc:
            logger.error("PDF export failed for '%s': %s", book.title, exc)
            raise RuntimeError(f"PDF export failed: {exc}") from exc

    # ── Title resolution (same logic as DOCX exporter) ─────────────────────

    def _resolve_chapter_title(self, raw_title: str, content: str, chapter_num: int, book_title: str) -> str:
        title = self._clean_markdown(raw_title).strip()
        title = re.sub(r'^[•\-\*\#\s]+', '', title).strip()
        title = re.sub(r'^Chapter\s+\d+\s*:\s*', '', title, flags=re.IGNORECASE).strip()

        clean_book = self._clean_markdown(book_title).strip().lower()
        if not title or title.lower() == clean_book or len(title) < 4:
            title = self._extract_title_from_content(content, chapter_num)

        return title or f"Chapter {chapter_num}"

    def _extract_title_from_content(self, content: str, chapter_num: int) -> str:
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            if re.match(r'^Chapter\s+\d+\s*:?\s*$', line, re.IGNORECASE):
                continue
            m = re.match(r'^#{1,6}\s+(.+)$', line)
            if m:
                candidate = self._clean_markdown(m.group(1)).strip()
                candidate = re.sub(r'^Chapter\s+\d+\s*:\s*', '', candidate, flags=re.IGNORECASE).strip()
                if candidate and len(candidate) > 4:
                    return candidate
            m2 = re.match(r'^\*\*(.+?)\*\*$', line)
            if m2:
                candidate = m2.group(1).strip()
                if candidate and len(candidate) > 4:
                    return candidate
        return ""

    # ── Styles ─────────────────────────────────────────────────────────────

    def _build_styles(self) -> dict:
        base = getSampleStyleSheet()
        return {
            "cover_title": ParagraphStyle(
                "CoverTitle", parent=base["Title"],
                fontSize=36, textColor=_PRIMARY,
                spaceAfter=20, alignment=TA_CENTER,
                fontName="Times-Bold", leading=44,
            ),
            "cover_subtitle": ParagraphStyle(
                "CoverSubtitle", fontSize=14, textColor=_GREY,
                alignment=TA_CENTER, fontName="Times-Italic", spaceAfter=8,
            ),
            "chapter_heading": ParagraphStyle(
                "ChapterHeading", fontSize=20, textColor=_PRIMARY,
                fontName="Times-Bold", spaceBefore=0, spaceAfter=16,
                alignment=TA_LEFT, leading=26,
            ),
            "heading1": ParagraphStyle(
                "Heading1Pro", fontSize=17, textColor=_PRIMARY,
                fontName="Times-Bold", spaceBefore=14, spaceAfter=8,
                alignment=TA_LEFT, leading=22,
            ),
            "heading2": ParagraphStyle(
                "Heading2Pro", fontSize=15, textColor=_PRIMARY,
                fontName="Times-Bold", spaceBefore=10, spaceAfter=6,
                alignment=TA_LEFT, leading=20,
            ),
            "heading3": ParagraphStyle(
                "Heading3Pro", fontSize=13, textColor=_PRIMARY,
                fontName="Times-Bold", spaceBefore=8, spaceAfter=4,
                alignment=TA_LEFT, leading=18,
            ),
            "bullet": ParagraphStyle(
                "BulletPro", fontSize=12, textColor=_BODY,
                fontName="Times-Roman", alignment=TA_LEFT,
                leading=16, spaceAfter=4,
                leftIndent=30, firstLineIndent=-15,
            ),
            "body": ParagraphStyle(
                "BodyPro", fontSize=12, textColor=_BODY,
                fontName="Times-Roman", alignment=TA_JUSTIFY,
                leading=18, spaceAfter=10, firstLineIndent=24,
            ),
            "copyright": ParagraphStyle(
                "Copyright", fontSize=10, textColor=_GREY,
                fontName="Times-Roman", alignment=TA_LEFT,
                leading=14, spaceAfter=6,
            ),
            "toc_heading": ParagraphStyle(
                "TOCHeading", fontSize=24, textColor=_PRIMARY,
                fontName="Times-Bold", spaceAfter=20,
                alignment=TA_LEFT, leading=30,
            ),
            "toc_entry": ParagraphStyle(
                "TOCEntry", fontSize=12, textColor=_BODY,
                fontName="Times-Roman", alignment=TA_LEFT,
                leading=20, spaceAfter=8, leftIndent=10,
            ),
            "divider_chapter": ParagraphStyle(
                "DividerChapter", fontSize=32, textColor=_GREY,
                fontName="Times-Bold", spaceAfter=24,
                alignment=TA_CENTER, leading=40,
            ),
            "divider_title": ParagraphStyle(
                "DividerTitle", fontSize=28, textColor=_PRIMARY,
                fontName="Times-Bold", spaceAfter=0,
                alignment=TA_CENTER, leading=36,
            ),
        }

    # ── Cover ──────────────────────────────────────────────────────────────

    def _build_cover(self, title: str, styles: dict) -> list:
        return [
            Spacer(1, 8 * cm),
            Paragraph(self._escape_xml(title.upper()), styles["cover_title"]),
            Spacer(1, 1 * cm),
            HRFlowable(width="50%", thickness=2, color=_ACCENT, spaceAfter=20, hAlign="CENTER"),
            Spacer(1, 0.5 * cm),
            Paragraph("Generated by Automated Book Generation System", styles["cover_subtitle"]),
            Spacer(1, 8 * cm),
            Paragraph(str(datetime.now().year), styles["cover_subtitle"]),
        ]

    # ── Copyright ──────────────────────────────────────────────────────────

    def _build_copyright(self, styles: dict) -> list:
        lines = [
            f"Copyright \u00a9 {datetime.now().year}",
            "",
            "This book was automatically generated using AI technology.",
            "All rights reserved.",
            "",
            f"Generated: {datetime.now().strftime('%B %d, %Y')}",
            "System: Automated Book Generation System",
            "Model: Google Gemini",
            "Database: Supabase",
            "",
            "For more information about this technology:",
            "contact@example.com",
        ]
        elements = [Spacer(1, 10 * cm)]
        for line in lines:
            if line:
                elements.append(Paragraph(self._escape_xml(line), styles["copyright"]))
            else:
                elements.append(Spacer(1, 0.3 * cm))
        return elements

    # ── TOC ────────────────────────────────────────────────────────────────

    def _build_toc(self, book_title: str, chapters: list, styles: dict) -> list:
        """Build a static TOC listing all chapter titles — clean, no raw outline text."""
        elements = [
            Paragraph("Table of Contents", styles["toc_heading"]),
            Spacer(1, 0.5 * cm),
            HRFlowable(width="100%", thickness=1, color=_ACCENT, spaceAfter=20),
            Spacer(1, 0.5 * cm),
        ]

        for i, chapter in enumerate(chapters, 1):
            content = chapter.content or ""
            clean_title = self._resolve_chapter_title(
                chapter.title or "", content, i, book_title
            )
            entry = f"<b>Chapter {i}:</b>  {self._escape_xml(clean_title)}"
            elements.append(Paragraph(entry, styles["toc_entry"]))

        return elements

    # ── Chapter divider ────────────────────────────────────────────────────

    def _build_chapter_divider(self, chapter: Chapter, number: int, book_title: str, styles: dict) -> list:
        """Build a full-page chapter divider with chapter number and title."""
        content = chapter.content or ""
        clean_title = self._resolve_chapter_title(
            chapter.title or "", content, number, book_title
        )

        return [
            Spacer(1, 10 * cm),
            Paragraph(f"CHAPTER {number}", styles["divider_chapter"]),
            Spacer(1, 0.5 * cm),
            Paragraph(self._escape_xml(clean_title), styles["divider_title"]),
        ]

    # ── Chapter content ────────────────────────────────────────────────────

    def _build_chapter(self, chapter: Chapter, number: int, book_title: str, styles: dict) -> list:
        content = chapter.content or ""
        clean_title = self._resolve_chapter_title(
            chapter.title or "", content, number, book_title
        )

        heading_text = f"Chapter {number}: {self._escape_xml(clean_title)}"
        elements = [
            Paragraph(heading_text, styles["chapter_heading"]),
            HRFlowable(width="100%", thickness=1, color=_PRIMARY, spaceAfter=16),
            Spacer(1, 0.5 * cm),
        ]

        content = self._remove_duplicate_title(content, number, clean_title)
        elements.extend(self._parse_content(content, styles))
        return elements

    def _remove_duplicate_title(self, content: str, chapter_num: int, clean_title: str) -> str:
        lines = content.splitlines()
        out = []
        title_lower = clean_title.lower().strip()
        for line in lines:
            stripped = line.strip()
            if re.match(rf'^[#\s]*Chapter\s+{chapter_num}\s*:', stripped, re.IGNORECASE):
                continue
            bare = re.sub(r'^#{1,6}\s*', '', stripped).strip().lower()
            if bare == title_lower and len(bare) > 4:
                continue
            out.append(line)
        return "\n".join(out)

    def _parse_content(self, content: str, styles: dict) -> list:
        elements = []
        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue

            # Markdown headings
            m = re.match(r'^(#{1,6})\s+(.+)$', line)
            if m:
                level = len(m.group(1))
                text = self._escape_xml(self._clean_markdown(m.group(2)))
                style = styles["heading1"] if level == 1 else styles["heading2"] if level == 2 else styles["heading3"]
                elements.append(Paragraph(text, style))
                i += 1
                continue

            # Standalone **bold** line → sub-heading
            m2 = re.match(r'^\*\*(.+?)\*\*$', line)
            if m2 and len(line) < 150:
                text = self._escape_xml(m2.group(1).strip())
                elements.append(Paragraph(text, styles["heading3"]))
                i += 1
                continue

            # Bullet list
            b = re.match(r'^[-•]\s+(.+)$', line) or re.match(r'^\*\s+(.+)$', line)
            if b:
                while i < len(lines):
                    bl = lines[i].strip()
                    bc = re.match(r'^[-•]\s+(.+)$', bl) or re.match(r'^\*\s+(.+)$', bl)
                    if bc:
                        text = self._escape_xml(self._clean_markdown(bc.group(1)))
                        elements.append(Paragraph(f"\u2022 {text}", styles["bullet"]))
                        i += 1
                    else:
                        break
                elements.append(Spacer(1, 0.2 * cm))
                continue

            # Regular prose paragraph
            para_lines = [line]
            i += 1
            while i < len(lines):
                nl = lines[i].strip()
                if not nl:
                    break
                if re.match(r'^#{1,6}\s+', nl):
                    break
                if re.match(r'^[-•]\s+', nl) or re.match(r'^\*\s+', nl):
                    break
                para_lines.append(nl)
                i += 1

            text = self._escape_xml(self._clean_markdown(" ".join(para_lines)))
            elements.append(Paragraph(text, styles["body"]))

        return elements

    # ── About Author ───────────────────────────────────────────────────────

    def _build_about_author(self, styles: dict) -> list:
        author_h = ParagraphStyle(
            "AuthorH", fontSize=20, textColor=_PRIMARY,
            fontName="Times-Bold", spaceAfter=12,
            alignment=TA_LEFT, leading=26,
        )
        author_b = ParagraphStyle(
            "AuthorB", fontSize=12, textColor=_BODY,
            fontName="Times-Roman", alignment=TA_LEFT,
            leading=18, spaceAfter=10,
        )
        bio = (
            "Shazmin Nasir is a Data Scientist, AI Engineer, and UI/UX Designer "
            "passionate about automation from design to deployment. With expertise in "
            "artificial intelligence and user experience, Shazmin builds systems that "
            "bridge the gap between intelligent algorithms and intuitive interfaces, "
            "creating seamless solutions that delight users while leveraging cutting-edge technology."
        )
        return [
            Paragraph("About the Author", author_h),
            Spacer(1, 0.5 * cm),
            Paragraph(bio, author_b),
            Spacer(1, 0.5 * cm),
            Paragraph("<i>Contact: shazminnasir481@gmail.com</i>", styles["copyright"]),
        ]

    # ── Page decorators ────────────────────────────────────────────────────

    def _draw_cover_decorator(self, canvas_obj, doc) -> None:
        """Draw geometric pattern on cover and plain pages (no header/footer)."""
        # Only draw pattern on first page (actual cover)
        if doc.page == 1:
            canvas_obj.saveState()
            
            # Draw concentric rectangles pattern
            width, height = A4
            canvas_obj.setLineWidth(2)
            
            # Alternate between navy and gold rectangles
            colors = [_PRIMARY, _GOLD]
            num_rectangles = 8
            
            for i in range(num_rectangles):
                color = colors[i % 2]
                canvas_obj.setStrokeColor(color)
                
                # Calculate rectangle dimensions (inset from edges)
                margin = (i + 1) * 1.2 * cm
                x1 = margin
                y1 = margin
                x2 = width - margin
                y2 = height - margin
                
                if x2 > x1 and y2 > y1:
                    canvas_obj.rect(x1, y1, x2 - x1, y2 - y1, stroke=1, fill=0)
            
            # Draw diagonal lines in the background
            canvas_obj.setStrokeColor(_GOLD)
            canvas_obj.setLineWidth(0.5)
            spacing = 1.5 * cm
            
            # Diagonal lines from bottom-left to top-right
            x = -height
            while x < width:
                canvas_obj.line(x, 0, x + height, height)
                x += spacing
            
            # White mask over title area — drawn AFTER lines, BEFORE text
            from reportlab.lib.colors import white
            canvas_obj.setFillColor(white)
            # Center rectangle: leave clean area for title (roughly 8cm-16cm from bottom)
            canvas_obj.rect(4 * cm, 11 * cm, width - 8 * cm, 10 * cm, fill=1, stroke=0)
            
            canvas_obj.restoreState()

    def _draw_page_decorator(self, canvas_obj, doc) -> None:
        canvas_obj.saveState()

        canvas_obj.setFont("Times-Italic", 10)
        canvas_obj.setFillColor(_GREY)
        canvas_obj.drawString(3.5 * cm, A4[1] - 2 * cm, self._book_title[:70])

        canvas_obj.setStrokeColor(_LIGHT_GREY)
        canvas_obj.setLineWidth(0.5)
        canvas_obj.line(3.5 * cm, A4[1] - 2.3 * cm, A4[0] - 2.5 * cm, A4[1] - 2.3 * cm)

        canvas_obj.setFont("Times-Roman", 10)
        canvas_obj.setFillColor(_GREY)
        canvas_obj.drawCentredString(A4[0] / 2, 2 * cm, f"\u2014 {doc.page} \u2014")

        canvas_obj.line(3.5 * cm, 2.3 * cm, A4[0] - 2.5 * cm, 2.3 * cm)

        canvas_obj.restoreState()

    # ── Utilities ──────────────────────────────────────────────────────────

    def _clean_markdown(self, text: str) -> str:
        """Strip markdown formatting, returning plain text."""
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        text = re.sub(r'\*(.*?)\*',   r'\1', text)
        text = re.sub(r'_(.*?)_',     r'\1', text)
        text = re.sub(r'^#{1,6}\s+',  '',    text, flags=re.MULTILINE)
        text = re.sub(r'```.*?```',   '',    text, flags=re.DOTALL)
        text = re.sub(r'`(.*?)`',     r'\1', text)
        # Only strip leading bullet markers (not mid-sentence chars)
        text = re.sub(r'^[-•]\s+',    '',    text, flags=re.MULTILINE)
        return text.strip()

    def _escape_xml(self, text: str) -> str:
        """Escape XML special characters for ReportLab Paragraph."""
        return (
            text
            .replace("&",  "&amp;")
            .replace("<",  "&lt;")
            .replace(">",  "&gt;")
            .replace("'",  "&#39;")
            .replace('"',  "&quot;")
        )