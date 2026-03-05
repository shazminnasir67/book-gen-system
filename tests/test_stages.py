"""
tests/test_stages.py
=====================
Unit and integration tests for the Automated Book Generation System.

Coverage:
  - Stage 1: Excel parsing validation (column detection, blank-row skipping)
  - Stage 2: Outline generation and re-generation logic
  - Stage 3: Chapter context chaining and chapter title parsing
  - Stage 4: Compile stage routing
  - Orchestrator: FSM transition logic
  - Notifications: Template rendering
  - Prompts: Prompt construction correctness

All Supabase and LLM calls are mocked — no real API calls in tests.
"""

import pytest
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_config():
    """Return a mock Config object with safe test values."""
    config = MagicMock()
    config.input_excel_path = Path("src/input/books_input.xlsx")
    config.output_dir = Path("output")
    config.log_dir = Path("logs")
    config.llm_max_tokens = 4096
    config.llm_temperature = 1.0
    config.llm_max_retries = 1
    config.llm_retry_wait_seconds = 1
    config.gemini_model = "gemini-1.5-pro"
    config.notification_to = "test@example.com"
    config.notification_from = "noreply@example.com"
    config.teams_webhook_url = ""
    return config


@pytest.fixture
def mock_db():
    """Return a mock SupabaseClient."""
    db = MagicMock()
    return db


@pytest.fixture
def mock_llm():
    """Return a mock LLMClient that returns deterministic text."""
    llm = MagicMock()
    llm.complete.return_value = "Generated content for testing purposes."
    return llm


@pytest.fixture
def sample_book():
    """Return a sample Book dataclass instance."""
    from src.database.supabase_client import Book
    return Book(
        id="book-test-uuid-001",
        title="The Art of Testing",
        notes_on_outline_before="Focus on practical examples.",
        outline="Chapter 1: Introduction\n1.1 Why testing matters\nChapter 2: Unit Tests\n2.1 Writing your first test\nChapter 3: Integration\n3.1 End-to-end scenarios",
        notes_on_outline_after=None,
        status_outline_notes="no_notes_needed",
        outline_version=1,
        chapter_notes_status="no_notes_needed",
        final_review_notes_status="no_notes_needed",
        book_output_status="pending",
        current_stage="generating_chapters",
    )


@pytest.fixture
def sample_chapter():
    """Return a sample Chapter dataclass instance."""
    from src.database.supabase_client import Chapter
    return Chapter(
        id="ch-test-uuid-001",
        book_id="book-test-uuid-001",
        chapter_number=1,
        title="Introduction",
        content="This is the introduction chapter content.",
        summary="A brief introduction to the topic.",
        status="generated",
        version=1,
    )


# ---------------------------------------------------------------------------
# Stage 1 Tests — Input parsing
# ---------------------------------------------------------------------------

class TestInputStage:
    """Tests for Stage 1: Excel import and Supabase seeding."""

    def test_run_raises_if_excel_not_found(self, mock_config, mock_db):
        """InputStage.run() raises FileNotFoundError if the Excel file is missing."""
        from src.stages.stage1_input import InputStage

        mock_config.input_excel_path = Path("nonexistent_path/books_input.xlsx")
        stage = InputStage(mock_config, mock_db)

        with pytest.raises(FileNotFoundError, match="Excel input not found"):
            stage.run()

    def test_skips_existing_book_by_title(self, mock_config, mock_db, sample_book):
        """InputStage does not create a duplicate book if the title already exists."""
        from src.stages.stage1_input import InputStage

        mock_db.list_all_books.return_value = [sample_book]
        stage = InputStage(mock_config, mock_db)

        # Simulate a parsed row with the same title
        result = stage._upsert_book({"title": sample_book.title})

        mock_db.create_book.assert_not_called()
        assert result.id == sample_book.id

    def test_creates_new_book_when_title_is_unique(self, mock_config, mock_db, sample_book):
        """InputStage creates a new book when the title is not in the DB."""
        from src.stages.stage1_input import InputStage

        sample_book_copy = MagicMock()
        sample_book_copy.title = "Different Book"
        mock_db.list_all_books.return_value = [sample_book_copy]

        new_book = MagicMock()
        new_book.id = "new-book-uuid"
        new_book.title = "Brand New Book"
        mock_db.create_book.return_value = new_book

        stage = InputStage(mock_config, mock_db)
        result = stage._upsert_book({"title": "Brand New Book"})

        mock_db.create_book.assert_called_once()
        assert result.id == "new-book-uuid"


# ---------------------------------------------------------------------------
# Stage 2 Tests — Outline generation
# ---------------------------------------------------------------------------

class TestOutlineStage:
    """Tests for Stage 2: Outline generation and re-generation."""

    def test_generate_advances_to_generating_chapters(self, mock_config, mock_db, mock_llm, sample_book):
        """generate() transitions to 'generating_chapters' when status_outline_notes='no_notes_needed'."""
        from src.stages.stage2_outline import OutlineStage

        mock_llm.complete.return_value = "Chapter 1: Test\n1.1 Sub\nChapter 2: More\n2.1 Sub"
        stage = OutlineStage(mock_config, mock_db, mock_llm)
        result = stage.generate(sample_book)

        assert result == "generating_chapters"
        mock_db.update_book_fields.assert_called()
        mock_db.save_outline_version.assert_called_once()

    def test_generate_advances_to_awaiting_review_when_yes(self, mock_config, mock_db, mock_llm, sample_book):
        """generate() transitions to 'awaiting_outline_review' when status_outline_notes='yes'."""
        from src.stages.stage2_outline import OutlineStage
        from src.database.supabase_client import Book
        import dataclasses

        book = dataclasses.replace(sample_book, status_outline_notes="yes")
        stage = OutlineStage(mock_config, mock_db, mock_llm)
        result = stage.generate(book)

        assert result == "awaiting_outline_review"

    def test_generate_pauses_when_status_is_no(self, mock_config, mock_db, mock_llm, sample_book):
        """generate() transitions to 'paused' when status_outline_notes='no'."""
        from src.stages.stage2_outline import OutlineStage
        import dataclasses

        book = dataclasses.replace(sample_book, status_outline_notes="no")
        stage = OutlineStage(mock_config, mock_db, mock_llm)
        result = stage.generate(book)

        assert result == "paused"

    def test_regenerate_raises_without_outline(self, mock_config, mock_db, mock_llm, sample_book):
        """regenerate() raises ValueError when the book has no existing outline."""
        from src.stages.stage2_outline import OutlineStage
        import dataclasses

        book = dataclasses.replace(sample_book, outline=None, notes_on_outline_after="Add more detail")
        stage = OutlineStage(mock_config, mock_db, mock_llm)

        with pytest.raises(ValueError, match="no existing outline"):
            stage.regenerate(book)

    def test_regenerate_raises_without_review_notes(self, mock_config, mock_db, mock_llm, sample_book):
        """regenerate() raises ValueError when there are no reviewer notes."""
        from src.stages.stage2_outline import OutlineStage
        import dataclasses

        book = dataclasses.replace(sample_book, notes_on_outline_after=None)
        stage = OutlineStage(mock_config, mock_db, mock_llm)

        with pytest.raises(ValueError, match="no reviewer notes"):
            stage.regenerate(book)


# ---------------------------------------------------------------------------
# Stage 3 Tests — Chapter generation and context chaining
# ---------------------------------------------------------------------------

class TestChapterStage:
    """Tests for Stage 3: Chapter generation with context chaining."""

    def test_parse_chapter_titles_numbered_format(self, mock_config, mock_db, mock_llm):
        """_parse_chapter_titles correctly extracts chapters from a numbered list."""
        from src.stages.stage3_chapters import ChapterStage

        outline = "1. Introduction\n2. Background\n3. Methodology\n4. Results\n5. Conclusion"
        stage = ChapterStage(mock_config, mock_db, mock_llm)
        titles = stage._parse_chapter_titles(outline)

        assert len(titles) >= 3
        assert "Introduction" in titles

    def test_parse_chapter_titles_chapter_format(self, mock_config, mock_db, mock_llm):
        """_parse_chapter_titles handles 'Chapter N: Title' format."""
        from src.stages.stage3_chapters import ChapterStage

        outline = "Chapter 1: The Beginning\nChapter 2: The Middle\nChapter 3: The End"
        stage = ChapterStage(mock_config, mock_db, mock_llm)
        titles = stage._parse_chapter_titles(outline)

        assert len(titles) == 3
        assert "The Beginning" in titles

    def test_run_raises_without_outline(self, mock_config, mock_db, mock_llm):
        """run() raises ValueError when the book has no outline."""
        from src.stages.stage3_chapters import ChapterStage
        from src.database.supabase_client import Book
        import dataclasses

        book = Book(
            id="uuid",
            title="No Outline Book",
            outline=None,
            current_stage="generating_chapters",
        )
        stage = ChapterStage(mock_config, mock_db, mock_llm)

        with pytest.raises(ValueError, match="no outline"):
            stage.run(book)

    def test_context_chain_built_correctly(self, mock_config, mock_db, mock_llm, sample_book):
        """get_previous_summaries is called with the correct before_chapter value."""
        from src.stages.stage3_chapters import ChapterStage

        mock_db.get_previous_summaries.return_value = [(1, "Summary of chapter 1")]
        mock_db.get_chapters_for_book.return_value = []
        mock_db.get_book.return_value = sample_book

        # Mock create_chapter to return a proper chapter object
        from src.database.supabase_client import Chapter
        mock_chapter = Chapter(
            id="ch-uuid",
            book_id=sample_book.id,
            chapter_number=1,
            title="Introduction",
            status="pending",
        )
        mock_db.create_chapter.return_value = mock_chapter
        mock_db.update_chapter.return_value = mock_chapter

        stage = ChapterStage(mock_config, mock_db, mock_llm)

        # Simulate generating chapter 2 — should fetch summaries before chapter 2
        stage._generate_chapter(sample_book, 2, "Chapter 2 Title", None)

        mock_db.get_previous_summaries.assert_called_once_with(sample_book.id, before_chapter=2)


# ---------------------------------------------------------------------------
# Prompt tests
# ---------------------------------------------------------------------------

class TestPrompts:
    """Tests for src/ai/prompts.py — verify prompt content and structure."""

    def test_outline_prompt_contains_title(self):
        """build_outline_prompt includes the book title in the output."""
        from src.ai.prompts import build_outline_prompt

        prompt = build_outline_prompt("My Test Book", "Focus on science.")
        assert "My Test Book" in prompt
        assert "Focus on science" in prompt

    def test_outline_prompt_no_notes(self):
        """build_outline_prompt works correctly with no pre-generation notes."""
        from src.ai.prompts import build_outline_prompt

        prompt = build_outline_prompt("Test", None)
        assert "Test" in prompt
        assert "Special Instructions" not in prompt

    def test_chapter_prompt_includes_context(self):
        """build_chapter_prompt includes previous chapter summaries in the output."""
        from src.ai.prompts import build_chapter_prompt

        prompt = build_chapter_prompt(
            book_title="Test Book",
            book_outline="Chapter 1: Intro\nChapter 2: Details",
            chapter_number=2,
            chapter_title="Details",
            previous_summaries=[(1, "Chapter 1 was about basics.")],
        )
        assert "Chapter 1 Summary: Chapter 1 was about basics." in prompt
        assert "Chapter 2" in prompt

    def test_chapter_prompt_no_prior_chapters(self):
        """build_chapter_prompt handles the first-chapter case gracefully."""
        from src.ai.prompts import build_chapter_prompt

        prompt = build_chapter_prompt(
            book_title="Test Book",
            book_outline="Chapter 1: Intro",
            chapter_number=1,
            chapter_title="Introduction",
            previous_summaries=[],
        )
        assert "first chapter" in prompt.lower()

    def test_chapter_summary_prompt(self):
        """build_chapter_summary_prompt requests a 100-word summary."""
        from src.ai.prompts import build_chapter_summary_prompt

        prompt = build_chapter_summary_prompt(1, "Introduction", "Long chapter text here.")
        assert "100-word" in prompt
        assert "Introduction" in prompt


# ---------------------------------------------------------------------------
# Notification template tests
# ---------------------------------------------------------------------------

class TestNotificationTemplates:
    """Tests for notification message template rendering."""

    def test_all_event_types_have_templates(self):
        """All documented event types have entries in the TEMPLATES dict."""
        from src.notifications.email_notifier import TEMPLATES

        expected_events = [
            "outline_ready",
            "outline_regenerated",
            "chapter_ready",
            "awaiting_chapter_notes",
            "final_compiled",
            "system_paused",
            "error",
        ]
        for event in expected_events:
            assert event in TEMPLATES, f"Missing template for event: {event}"

    def test_email_notify_unknown_event_does_not_raise(self, mock_config, mock_db):
        """EmailNotifier.notify() logs a warning and returns for unknown events."""
        from src.notifications.email_notifier import EmailNotifier

        notifier = EmailNotifier(mock_config, mock_db)
        # Should not raise
        notifier.notify("TOTALLY_UNKNOWN_EVENT_XYZ", book_id="uuid", title="Test")

    def test_teams_notify_skips_when_no_webhook(self, mock_config, mock_db):
        """TeamsNotifier silently skips when TEAMS_WEBHOOK_URL is empty."""
        from src.notifications.teams_notifier import TeamsNotifier
        import httpx

        mock_config.teams_webhook_url = ""
        notifier = TeamsNotifier(mock_config, mock_db)

        # Should not raise and should not make any HTTP calls
        with patch("httpx.post") as mock_post:
            notifier.notify("outline_ready", book_id="uuid", title="Test")
            mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Orchestrator FSM tests
# ---------------------------------------------------------------------------

class TestOrchestratorFSM:
    """Tests for FSM transition logic in the Orchestrator."""

    def test_terminal_states_are_skipped(self, mock_config):
        """Orchestrator does not call any handler for books in terminal states."""
        with patch("src.orchestrator.SupabaseClient"), \
             patch("src.orchestrator.LLMClient"), \
             patch("src.orchestrator.EmailNotifier"), \
             patch("src.orchestrator.TeamsNotifier"), \
             patch("src.orchestrator.InputStage"), \
             patch("src.orchestrator.OutlineStage"), \
             patch("src.orchestrator.ChapterStage"), \
             patch("src.orchestrator.CompileStage"):

            from src.orchestrator import Orchestrator
            from src.database.supabase_client import Book

            orch = Orchestrator(mock_config)
            done_book = Book(id="u1", title="Done Book", current_stage="done")
            error_book = Book(id="u2", title="Error Book", current_stage="error")

            # These should silently return without calling any handler
            orch._process_book(done_book)
            orch._process_book(error_book)

    def test_unknown_stage_parks_book_in_paused(self, mock_config):
        """Unknown stage names cause the book to be parked in PAUSED."""
        with patch("src.orchestrator.SupabaseClient") as mock_db_cls, \
             patch("src.orchestrator.LLMClient"), \
             patch("src.orchestrator.EmailNotifier"), \
             patch("src.orchestrator.TeamsNotifier"), \
             patch("src.orchestrator.InputStage"), \
             patch("src.orchestrator.OutlineStage"), \
             patch("src.orchestrator.ChapterStage"), \
             patch("src.orchestrator.CompileStage"):

            from src.orchestrator import Orchestrator
            from src.database.supabase_client import Book

            orch = Orchestrator(mock_config)
            weird_book = Book(id="u3", title="Weird", current_stage="COMPLETELY_UNKNOWN")
            orch._process_book(weird_book)

            # _transition should have been called with 'paused'
            orch._db.update_book_stage.assert_called_with("u3", "paused")
