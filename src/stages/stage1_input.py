"""
src/stages/stage1_input.py
==========================
Stage 1 — Input Ingestion

Responsibilities:
  - Read the Excel file (src/input/books_input.xlsx) using openpyxl.
  - Validate required columns and non-empty title fields.
  - For each valid row, check whether the book already exists in Supabase
    (matched by title) to avoid duplicate seeding.
  - Insert new books into the `books` table and set current_stage='input'.
  - Return a list of all seeded/existing Book records so the orchestrator
    can begin driving each through the FSM.
"""

import logging
from pathlib import Path
from typing import Optional

import openpyxl

from src.config import Config
from src.database.supabase_client import SupabaseClient, Book

logger = logging.getLogger(__name__)

# Expected column headers (order-independent lookup by name)
REQUIRED_COLUMNS = {"title"}
OPTIONAL_COLUMNS = {
    "notes_on_outline_before",
    "notes_on_outline_after",
    "status_outline_notes",
    "chapter_notes_status",
    "final_review_notes_status",
}


class InputStage:
    """
    Handles reading the Excel input file and seeding books into Supabase.

    The stage is idempotent: re-running it will not create duplicate books
    as long as titles are unique. Books that already exist are skipped.
    """

    def __init__(self, config: Config, db: SupabaseClient) -> None:
       
        self._config = config
        self._db = db

    def run(self) -> list[Book]:
    
        excel_path = self._config.input_excel_path
        logger.info("Stage 1 — Reading Excel: %s", excel_path)

        if not excel_path.exists():
            raise FileNotFoundError(
                f"Excel input not found at '{excel_path}'. "
                "Place your books_input.xlsx."
            )

        rows = self._read_excel(excel_path)
        logger.info("Found %d data rows in Excel", len(rows))

        results: list[Book] = []
        for row_data in rows:
            title = row_data.get("title", "").strip()
            if not title:
                logger.warning("Skipping row with empty title: %s", row_data)
                continue

            book = self._upsert_book(row_data)
            results.append(book)

        logger.info("Stage 1 complete — %d books ready for processing", len(results))
        return results

   
    def _read_excel(self, path: Path) -> list[dict]:
        """
        Open the Excel workbook and parse rows into dicts keyed by column name.

        Args:
            path: Absolute path to the .xlsx file.

        Returns:
            List of row dicts (one dict per data row, empty rows skipped).

        Raises:
            ValueError: If required columns are absent from the sheet header.
        """
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            logger.warning("Excel sheet is empty")
            return []

        # First row is the header
        headers = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
        missing = REQUIRED_COLUMNS - set(headers)
        if missing:
            raise ValueError(
                f"Excel file is missing required columns: {missing}. "
                f"Found headers: {headers}"
            )

        col_index = {h: i for i, h in enumerate(headers)}
        parsed: list[dict] = []

        for row in rows[1:]:
            if all(cell is None or str(cell).strip() == "" for cell in row):
                continue

            row_dict: dict = {}
            for col_name in [*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS]:
                if col_name in col_index:
                    val = row[col_index[col_name]]
                    row_dict[col_name] = str(val).strip() if val is not None else None
                else:
                    row_dict[col_name] = None
            parsed.append(row_dict)

        wb.close()
        return parsed

    def _upsert_book(self, row_data: dict) -> Book:
        """
        Insert a new book or return the existing one if the title already exists.

        Args:
            row_data: Parsed row dict from the Excel file.

        Returns:
            Book: Either the newly created or the pre-existing book record.
        """
        title = row_data["title"]

        # Check for an existing book with the same title
        existing_books = self._db.list_all_books()
        for existing in existing_books:
            if existing.title.strip().lower() == title.lower():
                logger.info("Book already exists — skipping seed: '%s'", title)
                return existing

        # Build the insertion payload
        payload: dict = {
            "title": title,
            "current_stage": "awaiting_input",
        }
        for col in OPTIONAL_COLUMNS:
            val = row_data.get(col)
            if val:
                payload[col] = val

        book = self._db.create_book(payload)
        logger.info("Seeded new book: '%s' (id=%s)", title, book.id)
        return book
