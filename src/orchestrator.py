"""
src/orchestrator.py
====================
Finite State Machine (FSM) Orchestrator for the Automated Book Generation System.

This is the brain of the system. It:
  1. Reads all non-terminal books from Supabase.
  2. For each book, evaluates its `current_stage` against the FSM transition table.
  3. Calls the appropriate stage handler.
  4. Writes the new stage back to Supabase.
  5. Fires email + Teams notifications on key events.
  6. Handles errors gracefully — parks the book in ERROR state and notifies.

The FSM is RESUMABLE: on every invocation, it re-reads DB state so that
a crash at any point can be recovered by simply re-running the orchestrator.

FSM States
----------
awaiting_input           → starting point after Excel seeding
generating_outline       → LLM outline generation in progress
awaiting_outline_review  → waiting for human to add notes_on_outline_after
regenerating_outline     → LLM regenerating outline with reviewer notes
generating_chapters      → chapter-by-chapter LLM generation
awaiting_chapter_review  → waiting for human to review a chapter
compiling                → DOCX + PDF export + upload
done                     → terminal success state
paused                   → human action required (check DB for reason)
error                    → terminal failure state (check logs)
"""

import logging
import time
from typing import Optional

from src.config import Config
from src.database.supabase_client import SupabaseClient, Book
from src.ai.llm_client import LLMClient
from src.stages.stage1_input import InputStage
from src.stages.stage2_outline import OutlineStage
from src.stages.stage3_chapters import ChapterStage
from src.stages.stage4_compile import CompileStage
from src.notifications.email_notifier import EmailNotifier
from src.notifications.teams_notifier import TeamsNotifier

logger = logging.getLogger(__name__)

# Seconds to wait between orchestrator ticks (when running in daemon mode)
DEFAULT_POLL_INTERVAL = 30

# Terminal states — the orchestrator will not process books in these states
TERMINAL_STATES = {"done", "error"}

# FSM transition map: current_stage → method name on Orchestrator
FSM_TRANSITIONS: dict[str, str] = {
    "awaiting_input":           "_handle_awaiting_input",
    "generating_outline":       "_handle_generating_outline",
    "awaiting_outline_review":  "_handle_awaiting_outline_review",
    "regenerating_outline":     "_handle_regenerating_outline",
    "generating_chapters":      "_handle_generating_chapters",
    "awaiting_chapter_review":  "_handle_awaiting_chapter_review",
    "compiling":                "_handle_compiling",
    "paused":                   "_handle_paused",
}


class Orchestrator:
    """
    FSM-driven pipeline orchestrator.

    Each public method corresponds to one FSM state handler. Transitions are
    written back to Supabase at the END of each handler, ensuring that a crash
    mid-handler leaves the book in its previous state (safe to retry).
    """

    def __init__(self, config: Config) -> None:
        """
        Initialise the orchestrator and all shared service objects.

        Args:
            config: Fully loaded application configuration.
        """
        self._config = config
        self._db = SupabaseClient(config)
        self._llm = LLMClient(config)
        self._email = EmailNotifier(config, self._db)
        self._teams = TeamsNotifier(config, self._db)

        # Stage handlers
        self._input_stage = InputStage(config, self._db)
        self._outline_stage = OutlineStage(config, self._db, self._llm)
        self._chapter_stage = ChapterStage(config, self._db, self._llm)
        self._compile_stage = CompileStage(config, self._db)

        logger.info("Orchestrator initialised")

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def run_once(self) -> None:
        """
        Execute one full tick of the orchestrator.

        Reads all non-terminal books from Supabase, evaluates each book's
        current FSM state, and advances it by one transition. Safe to call
        repeatedly in a polling loop.
        """
        logger.info("=== Orchestrator tick ===")

        try:
            books = self._db.list_pending_books()
        except Exception as exc:
            logger.error("Failed to fetch pending books: %s", exc)
            return

        if not books:
            logger.info("No pending books to process")
            return

        logger.info("Processing %d pending book(s)", len(books))
        for book in books:
            self._process_book(book)

    def run_input_stage(self) -> list[Book]:
        """
        Run Stage 1 (Excel → Supabase seeding) explicitly.

        Typically called once at startup before the main polling loop.

        Returns:
            List of Book records ready for FSM processing.
        """
        logger.info("Running input stage explicitly")
        books = self._input_stage.run()
        for book in books:
            if book.current_stage == "awaiting_input":
                self._transition(book, "generating_outline")
        return books

    def run_daemon(self, poll_interval: int = DEFAULT_POLL_INTERVAL) -> None:
        """
        Run the orchestrator in a continuous polling loop until interrupted.

        Args:
            poll_interval: Seconds between ticks.
        """
        logger.info("Daemon mode — polling every %ds (Ctrl+C to stop)", poll_interval)
        try:
            while True:
                self.run_once()
                logger.info("Sleeping for %ds...", poll_interval)
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            logger.info("Daemon mode stopped by user")

    # -----------------------------------------------------------------------
    # State handlers
    # -----------------------------------------------------------------------

    def _process_book(self, book: Book) -> None:
        """
        Dispatch a book to the correct FSM handler based on its current stage.

        Args:
            book: The book to process.
        """
        stage = book.current_stage
        handler_name = FSM_TRANSITIONS.get(stage)

        if stage in TERMINAL_STATES:
            logger.debug("Book '%s' is in terminal state '%s' — skipping", book.title, stage)
            return

        if not handler_name:
            logger.warning("Unknown stage '%s' for book='%s' — parking in PAUSED", stage, book.title)
            self._transition(book, "paused")
            return

        handler = getattr(self, handler_name, None)
        if not handler:
            logger.error("Handler '%s' not found on Orchestrator", handler_name)
            return

        try:
            logger.info("Book='%s' stage=%s → calling %s", book.title, stage, handler_name)
            handler(book)
        except Exception as exc:
            logger.exception("Error processing book='%s' at stage='%s': %s", book.title, stage, exc)
            self._transition(book, "error")
            self._notify_all(
                "error",
                book_id=book.id,
                title=book.title,
                stage=stage,
                message=str(exc),
            )

    def _handle_awaiting_input(self, book: Book) -> None:
        """
        Handle the AWAITING_INPUT state.

        If the book has a `notes_on_outline_before` value, it's ready for
        outline generation. Otherwise, it's stalled waiting for input data.

        Args:
            book: Book to evaluate.
        """
        if book.notes_on_outline_before:
            self._transition(book, "generating_outline")
        else:
            logger.info("Book='%s' still awaiting input data", book.title)

    def _handle_generating_outline(self, book: Book) -> None:
        """
        Handle the GENERATING_OUTLINE state — call the LLM and get an outline.

        Args:
            book: Book to generate an outline for.
        """
        next_stage = self._outline_stage.generate(book)
        self._transition(book, next_stage)

        if next_stage == "awaiting_outline_review":
            self._notify_all("outline_ready", book_id=book.id, title=book.title)
        elif next_stage == "paused":
            self._notify_all("system_paused", book_id=book.id, title=book.title, stage="generating_outline")

    def _handle_awaiting_outline_review(self, book: Book) -> None:
        """
        Handle the AWAITING_OUTLINE_REVIEW state.

        Checks DB for reviewer action:
          - notes_on_outline_after present → move to REGENERATING_OUTLINE
          - status_outline_notes == 'no_notes_needed' → move to GENERATING_CHAPTERS
          - otherwise → stay paused

        Args:
            book: Book to check.
        """
        # Re-fetch latest DB state to pick up any human edits
        fresh = self._db.get_book(book.id)
        if not fresh:
            return

        if fresh.notes_on_outline_after:
            self._transition(fresh, "regenerating_outline")
        elif fresh.status_outline_notes == "no_notes_needed":
            self._transition(fresh, "generating_chapters")
        else:
            logger.info("Book='%s' still awaiting outline review", fresh.title)

    def _handle_regenerating_outline(self, book: Book) -> None:
        """
        Handle the REGENERATING_OUTLINE state — re-generate using reviewer notes.

        Args:
            book: Book to regenerate outline for.
        """
        fresh = self._db.get_book(book.id)
        if not fresh:
            return

        next_stage = self._outline_stage.regenerate(fresh)
        self._transition(fresh, next_stage)
        version = fresh.outline_version + 1
        self._notify_all(
            "outline_regenerated",
            book_id=fresh.id,
            title=fresh.title,
            version=version,
        )

    def _handle_generating_chapters(self, book: Book) -> None:
        """
        Handle the GENERATING_CHAPTERS state — advance chapter generation.

        The chapter stage returns after each gate so the orchestrator can
        pause and notify before the next tick.

        Args:
            book: Book to continue chapter generation for.
        """
        fresh = self._db.get_book(book.id)
        if not fresh:
            return

        next_stage = self._chapter_stage.run(fresh)
        self._transition(fresh, next_stage)

        if next_stage == "awaiting_chapter_review":
            # Determine which chapter just triggered the gate
            chapters = self._db.get_chapters_for_book(fresh.id)
            last_generated = max(
                (c.chapter_number for c in chapters if c.status == "generated"),
                default=0,
            )
            self._notify_all(
                "chapter_ready",
                book_id=fresh.id,
                title=fresh.title,
                chapter_num=last_generated,
            )
        elif next_stage == "paused":
            self._notify_all("system_paused", book_id=fresh.id, title=fresh.title, stage="generating_chapters")
        elif next_stage == "compiling":
            pass  # advance immediately — compile handler fires next tick

    def _handle_awaiting_chapter_review(self, book: Book) -> None:
        """
        Handle the AWAITING_CHAPTER_REVIEW state.

        Checks for reviewer action on the latest chapter:
          - chapter_notes present → revise that chapter, return to GENERATING_CHAPTERS
          - status == 'no_notes_needed' → resume generation
          - otherwise → stay paused

        Args:
            book: Book to check.
        """
        fresh = self._db.get_book(book.id)
        if not fresh:
            return

        chapters = self._db.get_chapters_for_book(fresh.id)
        if not chapters:
            return

        # Find the last generated chapter that may have review notes
        last_ch = max(
            (c for c in chapters if c.status == "generated"),
            key=lambda c: c.chapter_number,
            default=None,
        )

        if last_ch and last_ch.chapter_notes:
            # Reviewer has added notes — revise this chapter
            self._chapter_stage.revise_chapter(fresh, last_ch.chapter_number)
            self._transition(fresh, "generating_chapters")
        elif fresh.chapter_notes_status == "no_notes_needed":
            self._transition(fresh, "generating_chapters")
        else:
            logger.info("Book='%s' still awaiting chapter review", fresh.title)

    def _handle_compiling(self, book: Book) -> None:
        """
        Handle the COMPILING state — produce and upload DOCX + PDF.

        Args:
            book: Book to compile.
        """
        next_stage = self._compile_stage.run(book)
        self._transition(book, next_stage)

        if next_stage == "done":
            self._notify_all("final_compiled", book_id=book.id, title=book.title)

    def _handle_paused(self, book: Book) -> None:
        """
        Handle the PAUSED state — re-evaluate conditions to resume.

        When a book is paused, re-check its DB fields to see if a human has
        provided the required input. If conditions are met, advance the stage.

        Args:
            book: Paused book to check.
        """
        fresh = self._db.get_book(book.id)
        if not fresh:
            return

        # Re-evaluate restart conditions
        if fresh.notes_on_outline_after:
            self._transition(fresh, "regenerating_outline")
        elif fresh.status_outline_notes == "no_notes_needed" and not fresh.outline:
            self._transition(fresh, "generating_outline")
        elif fresh.status_outline_notes == "no_notes_needed" and fresh.outline:
            self._transition(fresh, "generating_chapters")
        elif fresh.chapter_notes_status == "no_notes_needed":
            self._transition(fresh, "generating_chapters")
        else:
            logger.info("Book='%s' remains paused — no human input detected", fresh.title)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _transition(self, book: Book, new_stage: str) -> None:
        """
        Write a new FSM stage to the `books` table.

        Args:
            book: Book being transitioned.
            new_stage: Target stage name.
        """
        logger.info("FSM: '%s' %s → %s", book.title, book.current_stage, new_stage)
        self._db.update_book_stage(book.id, new_stage)

    def _notify_all(self, event_type: str, book_id: Optional[str] = None, **kwargs) -> None:
        """
        Fire notifications on both email and Teams channels.

        Args:
            event_type: Event key (maps to TEMPLATES in notifiers).
            book_id: Associated book UUID.
            **kwargs: Template variables forwarded to both notifiers.
        """
        self._email.notify(event_type, book_id=book_id, **kwargs)
        self._teams.notify(event_type, book_id=book_id, **kwargs)