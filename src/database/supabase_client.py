"""
src/database/supabase_client.py
===============================
Single-responsibility data access layer for the Automated Book Generation System.

ALL Supabase queries live here. No other module should directly interact with
the Supabase client. This enforces a clean separation between business logic
(stages, orchestrator) and persistence concerns.

Exposed surface:
  - Book CRUD: create_book, get_book, update_book_stage, update_book_fields, list_pending_books
  - Chapter CRUD: create_chapter, get_chapter, get_chapters_for_book,
                  update_chapter, get_previous_summaries
  - Outline versions: save_outline_version
  - Notifications: log_notification
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from supabase import create_client, Client

from src.config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class Book:
    """Represents a row in the `books` table."""
    id: str
    title: str
    notes_on_outline_before: Optional[str] = None
    outline: Optional[str] = None
    notes_on_outline_after: Optional[str] = None
    status_outline_notes: Optional[str] = None
    outline_version: int = 1
    chapter_notes_status: Optional[str] = None
    final_review_notes_status: Optional[str] = None
    book_output_status: str = "pending"
    current_stage: str = "awaiting_input"
    total_chapters: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Chapter:
    """Represents a row in the `chapters` table."""
    id: str
    book_id: str
    chapter_number: int
    title: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    chapter_notes: Optional[str] = None
    status: str = "pending"
    version: int = 1
    created_at: Optional[str] = None


@dataclass
class OutlineVersion:
    """Represents a row in the `outline_versions` table."""
    id: str
    book_id: str
    version: int
    outline: str
    notes_used: Optional[str] = None
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Client wrapper
# ---------------------------------------------------------------------------

class SupabaseClient:
    """
    Thin wrapper around the Supabase Python client.

    Provides domain-specific methods so that stage modules never touch raw
    SQL or Supabase query builders directly. Every public method is wrapped
    in try/except and logs errors before re-raising.
    """

    def __init__(self, config: Config) -> None:
        """
        Initialise and authenticate the Supabase client.

        Args:
            config: Loaded application configuration.
        """
        self._config = config
        self._client: Client = create_client(
            config.supabase_url,
            config.supabase_service_key,
        )
        logger.info("Supabase client initialised for URL: %s", config.supabase_url)

    # -----------------------------------------------------------------------
    # Book operations
    # -----------------------------------------------------------------------

    def create_book(self, data: dict[str, Any]) -> Book:
        """
        Insert a new row into the `books` table.

        Args:
            data: Dictionary of column→value pairs. `id` is generated if absent.

        Returns:
            Book: The newly created book record.

        Raises:
            RuntimeError: On Supabase insertion failure.
        """
        try:
            data.setdefault("id", str(uuid.uuid4()))
            resp = self._client.table("books").insert(data).execute()
            row = resp.data[0]
            logger.info("Created book id=%s title=%r", row["id"], row["title"])
            return _row_to_book(row)
        except Exception as exc:
            logger.error("Failed to create book: %s | data=%s", exc, data)
            raise RuntimeError(f"create_book failed: {exc}") from exc

    def get_book(self, book_id: str) -> Optional[Book]:
        """
        Fetch a single book by primary key.

        Args:
            book_id: UUID of the book.

        Returns:
            Book if found, None otherwise.
        """
        try:
            resp = (
                self._client.table("books")
                .select("*")
                .eq("id", book_id)
                .single()
                .execute()
            )
            return _row_to_book(resp.data) if resp.data else None
        except Exception as exc:
            logger.error("get_book failed for id=%s: %s", book_id, exc)
            raise RuntimeError(f"get_book failed: {exc}") from exc

    def update_book_fields(self, book_id: str, fields: dict[str, Any]) -> Book:
        """
        Update arbitrary fields on a book row.

        Args:
            book_id: Target book UUID.
            fields: Mapping of column names to new values.

        Returns:
            Book: The updated book record.
        """
        try:
            resp = (
                self._client.table("books")
                .update(fields)
                .eq("id", book_id)
                .execute()
            )
            row = resp.data[0]
            logger.info("Updated book id=%s fields=%s", book_id, list(fields.keys()))
            return _row_to_book(row)
        except Exception as exc:
            logger.error("update_book_fields failed for id=%s: %s", book_id, exc)
            raise RuntimeError(f"update_book_fields failed: {exc}") from exc

    def update_book_stage(self, book_id: str, stage: str) -> None:
        """
        Update only the `current_stage` field of a book.

        Args:
            book_id: Target book UUID.
            stage: New FSM stage name.
        """
        try:
            self._client.table("books").update({"current_stage": stage}).eq("id", book_id).execute()
            logger.info("Book id=%s → stage=%s", book_id, stage)
        except Exception as exc:
            logger.error("update_book_stage failed for id=%s: %s", book_id, exc)
            raise RuntimeError(f"update_book_stage failed: {exc}") from exc

    def list_pending_books(self) -> list[Book]:
        """
        Return all books that are NOT in 'done' or 'error' state.

        Returns:
            List of Book objects that the orchestrator should process.
        """
        try:
            resp = (
                self._client.table("books")
                .select("*")
                .not_.in_("current_stage", ["done", "error"])
                .execute()
            )
            return [_row_to_book(r) for r in resp.data]
        except Exception as exc:
            logger.error("list_pending_books failed: %s", exc)
            raise RuntimeError(f"list_pending_books failed: {exc}") from exc

    def list_all_books(self) -> list[Book]:
        """
        Return every book regardless of stage.

        Returns:
            List of all Book records.
        """
        try:
            resp = self._client.table("books").select("*").execute()
            return [_row_to_book(r) for r in resp.data]
        except Exception as exc:
            logger.error("list_all_books failed: %s", exc)
            raise RuntimeError(f"list_all_books failed: {exc}") from exc

    # -----------------------------------------------------------------------
    # Chapter operations
    # -----------------------------------------------------------------------

    def create_chapter(self, data: dict[str, Any]) -> Chapter:
        """
        Insert a new row into the `chapters` table.

        Args:
            data: Column→value mapping. `id` auto-generated if absent.

        Returns:
            Chapter: Newly inserted chapter record.
        """
        try:
            data.setdefault("id", str(uuid.uuid4()))
            resp = self._client.table("chapters").insert(data).execute()
            row = resp.data[0]
            logger.info(
                "Created chapter book_id=%s chapter=%s",
                row["book_id"],
                row["chapter_number"],
            )
            return _row_to_chapter(row)
        except Exception as exc:
            logger.error("create_chapter failed: %s | data=%s", exc, data)
            raise RuntimeError(f"create_chapter failed: {exc}") from exc

    def get_chapter(self, book_id: str, chapter_number: int) -> Optional[Chapter]:
        """
        Fetch a specific chapter by book and chapter number.

        Args:
            book_id: Parent book UUID.
            chapter_number: 1-based chapter index.

        Returns:
            Chapter if found, None otherwise.
        """
        try:
            resp = (
                self._client.table("chapters")
                .select("*")
                .eq("book_id", book_id)
                .eq("chapter_number", chapter_number)
                .single()
                .execute()
            )
            return _row_to_chapter(resp.data) if resp.data else None
        except Exception as exc:
            logger.error(
                "get_chapter failed for book=%s chapter=%s: %s",
                book_id, chapter_number, exc,
            )
            raise RuntimeError(f"get_chapter failed: {exc}") from exc

    def get_chapters_for_book(self, book_id: str) -> list[Chapter]:
        """
        Return all chapters for a book, sorted by chapter_number ascending.

        Args:
            book_id: Parent book UUID.

        Returns:
            Ordered list of Chapter records.
        """
        try:
            resp = (
                self._client.table("chapters")
                .select("*")
                .eq("book_id", book_id)
                .order("chapter_number", desc=False)
                .execute()
            )
            return [_row_to_chapter(r) for r in resp.data]
        except Exception as exc:
            logger.error("get_chapters_for_book failed for book=%s: %s", book_id, exc)
            raise RuntimeError(f"get_chapters_for_book failed: {exc}") from exc

    def update_chapter(self, chapter_id: str, fields: dict[str, Any]) -> Chapter:
        """
        Update arbitrary fields on a chapter row.

        Args:
            chapter_id: Target chapter UUID.
            fields: Column→value mapping.

        Returns:
            Chapter: The updated record.
        """
        try:
            resp = (
                self._client.table("chapters")
                .update(fields)
                .eq("id", chapter_id)
                .execute()
            )
            row = resp.data[0]
            logger.info("Updated chapter id=%s fields=%s", chapter_id, list(fields.keys()))
            return _row_to_chapter(row)
        except Exception as exc:
            logger.error("update_chapter failed for id=%s: %s", chapter_id, exc)
            raise RuntimeError(f"update_chapter failed: {exc}") from exc

    def get_previous_summaries(self, book_id: str, before_chapter: int) -> list[tuple[int, str]]:
        """
        Fetch summaries of all chapters with chapter_number < `before_chapter`.

        Used to build the context chain fed into the LLM before each new chapter.

        Args:
            book_id: Parent book UUID.
            before_chapter: Exclusive upper bound on chapter number.

        Returns:
            List of (chapter_number, summary) tuples, ordered ascending.
        """
        try:
            resp = (
                self._client.table("chapters")
                .select("chapter_number, summary")
                .eq("book_id", book_id)
                .lt("chapter_number", before_chapter)
                .not_.is_("summary", "null")
                .order("chapter_number", desc=False)
                .execute()
            )
            return [(r["chapter_number"], r["summary"]) for r in resp.data]
        except Exception as exc:
            logger.error(
                "get_previous_summaries failed for book=%s before=%s: %s",
                book_id, before_chapter, exc,
            )
            raise RuntimeError(f"get_previous_summaries failed: {exc}") from exc

    # -----------------------------------------------------------------------
    # Outline version operations
    # -----------------------------------------------------------------------

    def save_outline_version(self, book_id: str, version: int, outline: str, notes_used: Optional[str] = None) -> OutlineVersion:
        """
        Persist a snapshot of an outline in `outline_versions`.

        Args:
            book_id: Parent book UUID.
            version: Monotonically increasing version counter.
            outline: The full outline text.
            notes_used: The reviewer notes that triggered this generation, if any.

        Returns:
            OutlineVersion: The saved record.
        """
        try:
            data = {
                "id": str(uuid.uuid4()),
                "book_id": book_id,
                "version": version,
                "outline": outline,
                "notes_used": notes_used,
            }
            resp = self._client.table("outline_versions").insert(data).execute()
            row = resp.data[0]
            logger.info("Saved outline version=%s for book=%s", version, book_id)
            return OutlineVersion(
                id=row["id"],
                book_id=row["book_id"],
                version=row["version"],
                outline=row["outline"],
                notes_used=row.get("notes_used"),
                created_at=row.get("created_at"),
            )
        except Exception as exc:
            logger.error("save_outline_version failed for book=%s: %s", book_id, exc)
            raise RuntimeError(f"save_outline_version failed: {exc}") from exc

    # -----------------------------------------------------------------------
    # Notification log
    # -----------------------------------------------------------------------

    def log_notification(
        self,
        event_type: str,
        channel: str,
        payload: dict[str, Any],
        book_id: Optional[str] = None,
    ) -> None:
        """
        Insert a record into `notification_log` after a notification is sent.

        Args:
            event_type: Logical event name (e.g. 'outline_ready').
            channel: Delivery channel ('email' | 'teams').
            payload: Arbitrary JSON-serialisable metadata.
            book_id: Associated book UUID (optional).
        """
        try:
            data = {
                "id": str(uuid.uuid4()),
                "book_id": book_id,
                "event_type": event_type,
                "channel": channel,
                "payload": payload,
            }
            self._client.table("notification_log").insert(data).execute()
            logger.debug("Logged notification event=%s channel=%s", event_type, channel)
        except Exception as exc:
            # Notification logging failure should not crash the pipeline
            logger.warning("log_notification failed (non-fatal): %s", exc)

    # -----------------------------------------------------------------------
    # Supabase Storage helpers
    # -----------------------------------------------------------------------

    def upload_file(self, bucket: str, remote_path: str, local_path: str, content_type: str = "application/octet-stream") -> str:
        """
        Upload a local file to Supabase Storage.

        Args:
            bucket: Target storage bucket name.
            remote_path: Destination path inside the bucket.
            local_path: Absolute local filesystem path.
            content_type: MIME type for the uploaded file.

        Returns:
            str: Public URL of the uploaded object.

        Raises:
            RuntimeError: On upload failure.
        """
        try:
            with open(local_path, "rb") as fh:
                self._client.storage.from_(bucket).upload(
                    path=remote_path,
                    file=fh,
                    file_options={"content-type": content_type},
                )
            public_url = self._client.storage.from_(bucket).get_public_url(remote_path)
            logger.info("Uploaded %s → %s/%s", local_path, bucket, remote_path)
            return public_url
        except Exception as exc:
            logger.error("upload_file failed (%s/%s): %s", bucket, remote_path, exc)
            raise RuntimeError(f"upload_file failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Private row-mapping helpers
# ---------------------------------------------------------------------------

def _row_to_book(row: dict[str, Any]) -> Book:
    """Map a raw Supabase dict to a Book dataclass."""
    return Book(
        id=row["id"],
        title=row["title"],
        notes_on_outline_before=row.get("notes_on_outline_before"),
        outline=row.get("outline"),
        notes_on_outline_after=row.get("notes_on_outline_after"),
        status_outline_notes=row.get("status_outline_notes"),
        outline_version=row.get("outline_version", 1),
        chapter_notes_status=row.get("chapter_notes_status"),
        final_review_notes_status=row.get("final_review_notes_status"),
        book_output_status=row.get("book_output_status", "pending"),
        current_stage=row.get("current_stage", "awaiting_input"),
        total_chapters=row.get("total_chapters", 0),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _row_to_chapter(row: dict[str, Any]) -> Chapter:
    """Map a raw Supabase dict to a Chapter dataclass."""
    return Chapter(
        id=row["id"],
        book_id=row["book_id"],
        chapter_number=row["chapter_number"],
        title=row.get("title"),
        content=row.get("content"),
        summary=row.get("summary"),
        chapter_notes=row.get("chapter_notes"),
        status=row.get("status", "pending"),
        version=row.get("version", 1),
        created_at=row.get("created_at"),
    )