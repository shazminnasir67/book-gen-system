"""
src/stages/stage3_chapters.py
==============================
Stage 3 — Chapter Generation with Context Chaining

Responsibilities:
  - Parse the book outline to determine the chapter list.
  - For each chapter (in order):
      1. Fetch summaries of all previously completed chapters from Supabase.
      2. Build the chapter prompt with the full context chain.
      3. Call the LLM to generate the chapter content.
      4. Immediately generate a 100-word summary and persist both.
      5. Apply the chapter-notes gate: if `chapter_notes_status == 'yes'`,
         pause at AWAITING_CHAPTER_REVIEW and notify the caller.
  - Resume gracefully: skip any chapters already in 'generated' or 'approved' status.
  - After all chapters are done, check `final_review_notes_status` to decide
    whether to advance to COMPILING or stay PAUSED.

This module never constructs prompts — it uses src/ai/prompts.py.
This module never issues raw DB queries — it uses SupabaseClient.
"""

import logging
import re
from typing import Optional

from src.config import Config
from src.database.supabase_client import SupabaseClient, Book, Chapter
from src.ai.prompts import (
    build_chapter_prompt,
    build_chapter_summary_prompt,
    build_chapter_revision_prompt,
)
from src.ai.llm_client import LLMClient

logger = logging.getLogger(__name__)


class ChapterStage:
    """
    Drives chapter-by-chapter generation for a single book.

    Designed to be re-entrant: if the orchestrator calls `run()` on a book
    that already has some generated chapters, those are skipped and generation
    resumes from the first pending chapter.
    """

    def __init__(self, config: Config, db: SupabaseClient, llm: LLMClient) -> None:
        """
        Initialise the chapter stage with shared service objects.

        Args:
            config: Application configuration.
            db: Supabase data access client.
            llm: Anthropic LLM client.
        """
        self._config = config
        self._db = db
        self._llm = llm

    def run(self, book: Book) -> str:
        """
        Generate all pending chapters for the given book, one at a time.

        The method returns after each chapter that triggers a review gate
        (chapter_notes_status == 'yes'), allowing the orchestrator to pause
        and send a notification before the next orchestrator tick.

        Args:
            book: The Book record being processed.

        Returns:
            str: Next FSM stage:
                 - 'awaiting_chapter_review' if a chapter gate was hit.
                 - 'compiling' if all chapters are done.
                 - 'paused' if chapter_notes_status is 'no' or None.

        Raises:
            ValueError: If the book has no outline from which to extract chapters.
        """
        if not book.outline:
            raise ValueError(f"Book id={book.id} has no outline. Cannot generate chapters.")

        chapter_titles = self._parse_chapter_titles(book.outline)
        if not chapter_titles:
            raise ValueError(f"Book id={book.id}: could not parse any chapters from outline.")

        logger.info(
            "Starting chapter generation for book='%s' — %d chapters detected",
            book.title, len(chapter_titles),
        )

        # Ensure placeholder rows exist for all chapters
        self._ensure_chapter_rows(book.id, chapter_titles)

        # Fetch current chapter states
        chapters = self._db.get_chapters_for_book(book.id)
        chapter_map = {c.chapter_number: c for c in chapters}

        for chapter_num, chapter_title in enumerate(chapter_titles, start=1):
            chapter = chapter_map.get(chapter_num)

            # Skip already completed chapters
            if chapter and chapter.status in ("generated", "approved"):
                logger.info(
                    "Skipping chapter %d/'%s' (status=%s)",
                    chapter_num, chapter_title, chapter.status,
                )
                continue

            # Generate this chapter
            next_stage = self._generate_chapter(book, chapter_num, chapter_title, chapter)

            # If a gate was hit, return immediately — orchestrator will re-call
            if next_stage != "continue":
                return next_stage

        # All chapters complete
        return self._resolve_final_stage(book.final_review_notes_status)

    def revise_chapter(self, book: Book, chapter_number: int) -> str:
        """
        Revise a specific chapter based on stored reviewer notes.

        Called when the orchestrator detects a chapter with non-empty
        `chapter_notes` that needs to be regenerated.

        Args:
            book: Parent book record.
            chapter_number: 1-based chapter index to revise.

        Returns:
            str: Next FSM stage after revision (typically 'generating_chapters'
                 to continue with remaining chapters).
        """
        chapter = self._db.get_chapter(book.id, chapter_number)
        if chapter is None:
            raise ValueError(f"Chapter {chapter_number} not found for book id={book.id}")
        if not chapter.chapter_notes:
            raise ValueError(f"Chapter {chapter_number} has no reviewer notes to act on.")

        logger.info(
            "Revising chapter %d/'%s' for book='%s'",
            chapter_number, chapter.title, book.title,
        )

        prompt = build_chapter_revision_prompt(
            book_title=book.title,
            chapter_number=chapter_number,
            chapter_title=chapter.title or f"Chapter {chapter_number}",
            current_content=chapter.content or "",
            reviewer_notes=chapter.chapter_notes,
        )

        new_content = self._llm.complete(user_prompt=prompt)
        new_summary = self._generate_summary(chapter_number, chapter.title or "", new_content)

        self._db.update_chapter(
            chapter.id,
            {
                "content": new_content,
                "summary": new_summary,
                "status": "generated",
                "version": (chapter.version or 1) + 1,
                "chapter_notes": None,  # clear notes after applying
            },
        )

        logger.info("Chapter %d revised successfully (new version=%d)", chapter_number, (chapter.version or 1) + 1)
        return "generating_chapters"

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _generate_chapter(
        self,
        book: Book,
        chapter_num: int,
        chapter_title: str,
        existing_chapter: Optional[Chapter],
    ) -> str:
        """
        Generate content for a single chapter and persist it.

        Args:
            book: Parent book record.
            chapter_num: 1-based chapter index.
            chapter_title: Chapter title from the outline.
            existing_chapter: Existing Chapter row if it exists (may be None).

        Returns:
            str: 'continue' to keep looping, or a FSM stage name to halt.
        """
        logger.info("Generating chapter %d: '%s'", chapter_num, chapter_title)

        # Fetch context chain
        summaries = self._db.get_previous_summaries(book.id, before_chapter=chapter_num)
        logger.debug("Context chain — %d previous summaries loaded", len(summaries))

        prompt = build_chapter_prompt(
            book_title=book.title,
            book_outline=book.outline,
            chapter_number=chapter_num,
            chapter_title=chapter_title,
            previous_summaries=summaries,
        )

        content = self._llm.complete(user_prompt=prompt)
        logger.info("Chapter %d content generated — %d chars", chapter_num, len(content))

        # Generate summary for context chaining
        summary = self._generate_summary(chapter_num, chapter_title, content)

        # Persist
        if existing_chapter:
            self._db.update_chapter(
                existing_chapter.id,
                {
                    "title": chapter_title,
                    "content": content,
                    "summary": summary,
                    "status": "generated",
                },
            )
        else:
            self._db.create_chapter(
                {
                    "book_id": book.id,
                    "chapter_number": chapter_num,
                    "title": chapter_title,
                    "content": content,
                    "summary": summary,
                    "status": "generated",
                }
            )

        logger.info("Chapter %d persisted", chapter_num)

        # Evaluate chapter gate
        refreshed_book = self._db.get_book(book.id)
        if refreshed_book and refreshed_book.chapter_notes_status == "yes":
            logger.info("Chapter gate hit (chapter_notes_status='yes') after chapter %d", chapter_num)
            return "awaiting_chapter_review"
        elif refreshed_book and refreshed_book.chapter_notes_status in (None, "no"):
            logger.info("Chapter paused (chapter_notes_status='%s') after chapter %d",
                        refreshed_book.chapter_notes_status, chapter_num)
            return "paused"

        return "continue"

    def _generate_summary(self, chapter_num: int, chapter_title: str, content: str) -> str:
        """
        Generate a 100-word summary of a chapter for use in context chaining.

        Args:
            chapter_num: 1-based chapter number.
            chapter_title: Chapter title.
            content: Full chapter prose.

        Returns:
            str: The generated summary text.
        """
        prompt = build_chapter_summary_prompt(
            chapter_number=chapter_num,
            chapter_title=chapter_title,
            chapter_content=content,
        )
        summary = self._llm.complete(user_prompt=prompt, max_tokens=300)
        logger.debug("Summary for chapter %d: %d chars", chapter_num, len(summary))
        return summary

    def _parse_chapter_titles(self, outline: str) -> list[str]:
        """
        Extract chapter titles from the outline text.

        The outline prompt enforces 'Chapter N: Title' format. No hardcoded
        cap — chapter count is fully dynamic based on the LLM output.

        Returns:
            Ordered list of chapter title strings (number prefix stripped).
        """
        # Primary: "Chapter N: Title" or "Chapter N - Title"
        primary = re.compile(
            r'^\s*Chapter\s+(\d+)\s*[:\-\u2013\u2014]\s*(.+)$',
            re.IGNORECASE | re.MULTILINE,
        )
        matches = primary.findall(outline)
        if matches:
            sorted_matches = sorted(matches, key=lambda x: int(x[0]))
            titles = [t[1].strip() for t in sorted_matches if len(t[1].strip()) > 4]
            if len(titles) >= 2:
                logger.debug("Parsed %d chapter titles (primary pattern)", len(titles))
                return titles

        # Fallback A: "1. Title" or "1) Title"
        fallback_a = re.compile(r'^\s*\d+[.)\s]\s+(.+)$', re.MULTILINE)
        titles_a = [m.strip() for m in fallback_a.findall(outline) if len(m.strip()) > 4]
        if len(titles_a) >= 2:
            logger.debug("Parsed %d chapter titles (fallback A)", len(titles_a))
            return titles_a

        # Fallback B: "# Heading" markdown
        fallback_b = re.compile(r'^#{1,3}\s+(.+)$', re.MULTILINE)
        titles_b = [m.strip() for m in fallback_b.findall(outline) if len(m.strip()) > 4]
        if len(titles_b) >= 2:
            logger.debug("Parsed %d chapter titles (fallback B)", len(titles_b))
            return titles_b

        logger.warning("Could not parse chapter titles from outline \u2014 no chapters will be generated")
        return []

    def _ensure_chapter_rows(self, book_id: str, chapter_titles: list[str]) -> None:
        """
        Pre-create placeholder chapter rows so the chapter list is stable.

        This is idempotent — existing rows (matched by chapter_number) are left
        untouched. This ensures that if generation restarts mid-book, previously
        generated chapters are not overwritten.

        Args:
            book_id: Parent book UUID.
            chapter_titles: Ordered list of chapter titles from the outline.
        """
        existing = {c.chapter_number for c in self._db.get_chapters_for_book(book_id)}
        for i, title in enumerate(chapter_titles, start=1):
            if i not in existing:
                self._db.create_chapter(
                    {
                        "book_id": book_id,
                        "chapter_number": i,
                        "title": title,
                        "status": "pending",
                    }
                )
        logger.debug("Chapter rows ensured for book_id=%s", book_id)

    def _resolve_final_stage(self, final_review_notes_status: Optional[str]) -> str:
        """
        Determine the FSM stage after all chapters have been generated.

        Args:
            final_review_notes_status: Value of the books table column.

        Returns:
            str: 'compiling' or 'paused'.
        """
        if final_review_notes_status == "no_notes_needed":
            logger.info("All chapters done — advancing to COMPILING")
            return "compiling"
        else:
            logger.info("All chapters done — parking at PAUSED (final_review_notes_status=%s)",
                        final_review_notes_status)
            return "paused"