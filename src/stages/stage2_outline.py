"""
src/stages/stage2_outline.py
============================
Stage 2 — Outline Generation & Re-generation

Responsibilities:
  - Generate the initial book outline via the LLM using pre-generation notes.
  - Persist the outline and its version in Supabase.
  - Evaluate the `status_outline_notes` field to decide next FSM state:
      * 'no_notes_needed' → advance to GENERATING_CHAPTERS (no review loop)
      * 'yes'            → pause at AWAITING_OUTLINE_REVIEW (notify reviewer)
      * 'no' / null      → park at PAUSED
  - Handle re-generation when the book is in REGENERATING_OUTLINE state:
      reads `notes_on_outline_after`, feeds it to the LLM, increments version.
  - Every outline is archived in `outline_versions` for auditability.

This module never constructs prompts — it delegates to src/ai/prompts.py.
This module never issues raw DB queries — it delegates to SupabaseClient.
"""

import logging
import re
from typing import Optional

from src.config import Config
from src.database.supabase_client import SupabaseClient, Book
from src.ai.prompts import build_outline_prompt, build_outline_regeneration_prompt
from src.ai.llm_client import LLMClient

logger = logging.getLogger(__name__)


class OutlineStage:
    """
    Manages outline generation and re-generation for a single book.

    This class is instantiated once per orchestrator tick and is stateless
    between runs — all persistent state lives in Supabase.
    """

    def __init__(self, config: Config, db: SupabaseClient, llm: LLMClient) -> None:
        """
        Initialise the outline stage with shared service objects.

        Args:
            config: Application configuration.
            db: Supabase data access client.
            llm: Anthropic LLM client for text generation.
        """
        self._config = config
        self._db = db
        self._llm = llm

    def generate(self, book: Book) -> str:
        """
        Generate the initial outline for a book in GENERATING_OUTLINE state.

        Workflow:
          1. Build the outline prompt using pre-generation notes.
          2. Call the LLM.
          3. Persist the outline text and current version to `books`.
          4. Archive it in `outline_versions`.
          5. Return the new FSM state based on `status_outline_notes`.

        Args:
            book: The Book record being processed.

        Returns:
            str: The new FSM stage name for the orchestrator to set.
        """
        logger.info("Generating outline for book='%s' (id=%s)", book.title, book.id)

        prompt = build_outline_prompt(
            title=book.title,
            notes_before=book.notes_on_outline_before,
        )

        outline_text = self._llm.complete(user_prompt=prompt)
        logger.info("Outline generated for book='%s' — length=%d chars", book.title, len(outline_text))

        # Parse chapter count from the outline so DB always has accurate total_chapters
        chapter_titles = self._parse_chapter_titles(outline_text)
        total_chapters = len(chapter_titles)
        logger.info("Outline parsed — %d chapters detected for book='%s'", total_chapters, book.title)

        # Persist outline + dynamic chapter count to books table
        self._db.update_book_fields(book.id, {
            "outline": outline_text,
            "total_chapters": total_chapters,
        })

        # Archive the version
        self._db.save_outline_version(
            book_id=book.id,
            version=book.outline_version,
            outline=outline_text,
            notes_used=book.notes_on_outline_before,
        )

        # Determine next FSM state
        next_stage = self._resolve_next_stage_after_outline(book.status_outline_notes)
        logger.info("Book='%s' post-outline → stage=%s", book.title, next_stage)
        return next_stage

    def regenerate(self, book: Book) -> str:
        """
        Re-generate the outline for a book in REGENERATING_OUTLINE state.

        Uses `notes_on_outline_after` as reviewer feedback. Increments the
        outline version counter before generating so each revision is uniquely
        archived.

        Args:
            book: The Book record being processed.

        Returns:
            str: The new FSM stage name ('awaiting_outline_review').

        Raises:
            ValueError: If the book has no outline to revise or no reviewer notes.
        """
        if not book.outline:
            raise ValueError(f"Book id={book.id} has no existing outline to revise.")
        if not book.notes_on_outline_after:
            raise ValueError(f"Book id={book.id} has no reviewer notes for outline revision.")

        new_version = book.outline_version + 1
        logger.info(
            "Re-generating outline v%d for book='%s' (id=%s)",
            new_version, book.title, book.id,
        )

        prompt = build_outline_regeneration_prompt(
            title=book.title,
            previous_outline=book.outline,
            reviewer_notes=book.notes_on_outline_after,
            notes_before=book.notes_on_outline_before,
        )

        outline_text = self._llm.complete(user_prompt=prompt)
        logger.info(
            "Outline v%d generated for book='%s' — length=%d chars",
            new_version, book.title, len(outline_text),
        )

        # Re-parse chapter count (editor may have added/removed chapters)
        chapter_titles = self._parse_chapter_titles(outline_text)
        total_chapters = len(chapter_titles)
        logger.info(
            "Outline v%d parsed — %d chapters detected for book='%s'",
            new_version, total_chapters, book.title,
        )

        # Persist updated outline, version, and refreshed chapter count
        self._db.update_book_fields(
            book.id,
            {
                "outline": outline_text,
                "outline_version": new_version,
                "total_chapters": total_chapters,
                # Clear the 'after' notes so they are not re-applied next cycle
                "notes_on_outline_after": None,
            },
        )

        # Archive this version
        self._db.save_outline_version(
            book_id=book.id,
            version=new_version,
            outline=outline_text,
            notes_used=book.notes_on_outline_after,
        )

        # After re-generation, always return to AWAITING_OUTLINE_REVIEW
        return "awaiting_outline_review"

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------


    def _parse_chapter_titles(self, outline: str) -> list[str]:
        """
        Extract chapter titles from the outline text.

        The outline prompt now enforces 'Chapter N: Title' format, so the
        primary pattern is very reliable. Fallback patterns handle any
        deviation. There is NO hardcoded cap — the list length equals however
        many chapters the LLM decided the topic needs.

        Args:
            outline: The full outline text returned by the LLM.

        Returns:
            Ordered list of clean chapter title strings (number prefix removed).
        """
        # Primary: "Chapter N: Title" — matches the enforced prompt format
        primary = re.compile(
            r'^\s*Chapter\s+(\d+)\s*[:\-\u2013\u2014]\s*(.+)$',
            re.IGNORECASE | re.MULTILINE,
        )
        matches = primary.findall(outline)
        if matches:
            # Sort by chapter number to handle any out-of-order matches
            sorted_matches = sorted(matches, key=lambda m: int(m[0]))
            titles = [m[1].strip() for m in sorted_matches if len(m[1].strip()) > 4]
            if len(titles) >= 2:
                logger.debug("Parsed %d chapter titles (primary pattern)", len(titles))
                return titles

        # Fallback: numbered list "1. Title" or "1) Title"
        fallback = re.compile(r'^\s*\d+[.)\s]\s+(.+)$', re.MULTILINE)
        matches2 = fallback.findall(outline)
        titles2 = [m.strip() for m in matches2 if len(m.strip()) > 4]
        if len(titles2) >= 2:
            logger.debug("Parsed %d chapter titles (fallback pattern)", len(titles2))
            return titles2

        logger.warning("Could not reliably parse chapter titles from outline — returning empty list")
        return []

    def _resolve_next_stage_after_outline(self, status_outline_notes: Optional[str]) -> str:
        """
        Map the `status_outline_notes` field to the appropriate FSM state.

        Args:
            status_outline_notes: Value from the books table.

        Returns:
            str: Target FSM stage name.
        """
        if status_outline_notes == "no_notes_needed":
            return "generating_chapters"
        elif status_outline_notes == "yes":
            return "awaiting_outline_review"
        else:
            # 'no', None, or any unexpected value → park
            return "paused"