"""
src/stages/stage4_compile.py
============================
Stage 4 — Compilation

Responsibilities:
  - Retrieve all generated chapters from Supabase in order.
  - Build the final DOCX and PDF files using the exporters.
  - Upload both files to Supabase Storage.
  - Update `book_output_status` and persist the public URLs in the book record.
  - Advance the book to 'done' stage.

This stage delegates all formatting logic to src/exporters/ and all
persistence logic to SupabaseClient — it only orchestrates the pipeline.
"""

import logging
from pathlib import Path

from src.config import Config
from src.database.supabase_client import SupabaseClient, Book
from src.exporters.docx_exporter_pro import DocxExporterPro
from src.exporters.pdf_exporter_pro import PdfExporterPro

logger = logging.getLogger(__name__)

# Supabase Storage bucket name — create this in your Supabase dashboard
STORAGE_BUCKET = "book-outputs"


class CompileStage:
    """
    Assembles the final DOCX and PDF for a completed book and uploads them
    to Supabase Storage.
    """

    def __init__(self, config: Config, db: SupabaseClient) -> None:
        """
        Initialise the compile stage.

        Args:
            config: Application configuration (for output directory paths).
            db: Supabase data access client.
        """
        self._config = config
        self._db = db
        self._docx_exporter = DocxExporterPro(config)
        self._pdf_exporter = PdfExporterPro(config)

    def run(self, book: Book) -> str:
        """
        Compile all chapters into DOCX + PDF and upload to Supabase Storage.

        Args:
            book: The Book record to compile. Must have all chapters in
                  'generated' or 'approved' status.

        Returns:
            str: 'done' on success.

        Raises:
            ValueError: If there are no chapters to compile.
            RuntimeError: On export or upload failure.
        """
        logger.info("Stage 4 — Compiling book='%s' (id=%s)", book.title, book.id)

        chapters = self._db.get_chapters_for_book(book.id)
        if not chapters:
            raise ValueError(f"Book id={book.id} has no chapters to compile.")

        # Filter to only generated/approved chapters
        ready_chapters = [c for c in chapters if c.status in ("generated", "approved")]
        logger.info(
            "Compiling %d chapters for book='%s'",
            len(ready_chapters), book.title,
        )

        # Ensure output directory exists
        output_dir = self._config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_title = _sanitize_filename(book.title)
        docx_path = output_dir / f"{safe_title}.docx"
        pdf_path = output_dir / f"{safe_title}.pdf"

        # Export DOCX
        self._docx_exporter.export(book, ready_chapters, str(docx_path))
        logger.info("DOCX written: %s", docx_path)

        # Export PDF
        self._pdf_exporter.export(book, ready_chapters, str(pdf_path))
        logger.info("PDF written: %s", pdf_path)

        # Upload to Supabase Storage
        docx_url = self._upload_file(book.id, str(docx_path), f"{safe_title}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        pdf_url = self._upload_file(book.id, str(pdf_path), f"{safe_title}.pdf", "application/pdf")

        # Persist URLs and mark as complete
        self._db.update_book_fields(
            book.id,
            {
                "book_output_status": "completed",
                "current_stage": "done",
            },
        )

        logger.info(
            "Book '%s' compiled and uploaded — DOCX=%s PDF=%s",
            book.title, docx_url, pdf_url,
        )
        return "done"

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _upload_file(self, book_id: str, local_path: str, filename: str, content_type: str) -> str:
        """
        Upload a local file to Supabase Storage under a book-scoped path.

        Args:
            book_id: Book UUID used as the storage directory prefix.
            local_path: Absolute local path to the file.
            filename: Desired filename in the storage bucket.
            content_type: MIME type of the file.

        Returns:
            str: Public URL of the uploaded file.
        """
        remote_path = f"{book_id}/{filename}"
        return self._db.upload_file(STORAGE_BUCKET, remote_path, local_path, content_type)


def _sanitize_filename(title: str) -> str:
    """
    Convert a book title into a safe filesystem filename.

    Replaces spaces with underscores and strips characters that are
    invalid in Windows filenames.

    Args:
        title: Raw book title.

    Returns:
        str: Sanitised filename without extension.
    """
    import re
    safe = re.sub(r'[<>:"/\\|?*]', "", title)
    safe = safe.replace(" ", "_")
    safe = safe[:100]  # cap length
    return safe or "untitled"
