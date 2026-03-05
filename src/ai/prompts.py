"""
src/ai/prompts.py
=================
Central prompt repository for the Automated Book Generation System.

ALL LLM prompts live here. Stage modules must import from this module and
must NEVER embed prompt text inline. This separation makes it trivial to
iterate on prompt quality without touching business logic.

Each public function returns a fully-rendered string ready to pass to the
LLM as a `user` message. System instructions live in SYSTEM_PROMPT.
"""

from typing import Optional


# ---------------------------------------------------------------------------
# System Prompt (shared across all calls)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert book author and editor. You write in a clear,
engaging, and professional style. You follow instructions precisely and produce
well-structured, publication-quality content. When asked to generate an outline
you produce a numbered, hierarchical structure. When asked to generate chapter
content you write fully developed prose — not bullet points or placeholders.
When asked for a summary you are concise and accurate."""


# ---------------------------------------------------------------------------
# Outline prompts
# ---------------------------------------------------------------------------

def build_outline_prompt(
    title: str,
    notes_before: Optional[str] = None,
) -> str:
    """
    Build the prompt for initial outline generation.

    Args:
        title: The book title as provided by the user.
        notes_before: Optional pre-generation notes from the book record
                      that should guide the outline structure.

    Returns:
        Fully rendered user prompt string.
    """
    notes_section = ""
    if notes_before and notes_before.strip():
        notes_section = f"""
## Special Instructions / Notes
{notes_before.strip()}

Please incorporate these instructions carefully into the outline structure.
"""

    return f"""Generate a detailed book outline for the following title:

# {title}
{notes_section}
## Requirements
- Decide the number of chapters that best suits this topic (typically 6–14).
  Do NOT use a fixed number — let the subject's natural scope determine it.
- Provide a compelling Introduction section before Chapter 1.
- Each chapter must have a clear, descriptive title (not generic like "Chapter 1").
- Each chapter should have 3–5 sub-sections with lettered labels (A, B, C…).
- Include a Conclusion chapter and appendices if the topic warrants them.
- Format STRICTLY as shown so the system can parse chapter titles automatically:

  Chapter 1: [Descriptive Title Here]
    A. Sub-section one
    B. Sub-section two

  Chapter 2: [Descriptive Title Here]
    A. Sub-section one
    ...

- Every chapter title must be unique and meaningful — no placeholders.

Return ONLY the outline text — no preamble, no commentary, no markdown code fences.
"""


def build_outline_regeneration_prompt(
    title: str,
    previous_outline: str,
    reviewer_notes: str,
    notes_before: Optional[str] = None,
) -> str:
    """
    Build the prompt for regenerating an outline after reviewer feedback.

    Args:
        title: The book title.
        previous_outline: The outline text from the previous version.
        reviewer_notes: The human reviewer's feedback notes.
        notes_before: Original pre-generation notes, if any.

    Returns:
        Fully rendered user prompt string.
    """
    original_notes_section = ""
    if notes_before and notes_before.strip():
        original_notes_section = f"""
## Original Author Instructions
{notes_before.strip()}
"""

    return f"""You previously generated an outline for a book titled "{title}".
A human reviewer has provided feedback that you must address.
{original_notes_section}
## Previous Outline
{previous_outline}

## Reviewer Feedback (must be fully addressed)
{reviewer_notes.strip()}

## Task
Revise the outline to fully address all reviewer feedback while maintaining
the overall quality and structure. Return ONLY the revised outline — no
preamble or commentary.
"""


# ---------------------------------------------------------------------------
# Chapter prompts
# ---------------------------------------------------------------------------

def build_chapter_prompt(
    book_title: str,
    book_outline: str,
    chapter_number: int,
    chapter_title: str,
    previous_summaries: list[tuple[int, str]],
) -> str:
    """
    Build the prompt for generating a single chapter with context chaining.

    Args:
        book_title: Parent book title.
        book_outline: The full book outline so the LLM understands the arc.
        chapter_number: 1-based index of the chapter to generate.
        chapter_title: Title of the target chapter.
        previous_summaries: List of (chapter_number, summary) tuples for all
                            previously generated chapters (1..N-1).

    Returns:
        Fully rendered user prompt string.
    """
    # Build context block from previous summaries (context chaining)
    if previous_summaries:
        context_lines = [
            f"Chapter {num} Summary: {summary.strip()}"
            for num, summary in previous_summaries
        ]
        context_block = "## Previously Written Chapters (for continuity)\n" + "\n\n".join(context_lines)
    else:
        context_block = "## Previously Written Chapters\nThis is the first chapter — no prior content."

    return f"""You are writing Chapter {chapter_number} of the book "{book_title}".

## Full Book Outline (for reference)
{book_outline}

{context_block}

## Your Task
Write Chapter {chapter_number}: {chapter_title}

Guidelines:
- Write fully developed prose (NOT bullet points or headers for the body text).
- The chapter should be 1,500–3,000 words.
- Maintain narrative continuity with the previously written chapters.
- Follow the sub-sections for this chapter as listed in the outline.
- Write in an engaging, authoritative tone appropriate for the subject matter.
- Begin directly with the chapter content (no preamble like "Here is Chapter N:").
- End the chapter with a brief transition sentence that flows into the next topic.

Return ONLY the chapter content.
"""


def build_chapter_summary_prompt(
    chapter_number: int,
    chapter_title: str,
    chapter_content: str,
) -> str:
    """
    Build the prompt for generating a 100-word summary of a completed chapter.

    This summary is stored in the DB and used as context for all subsequent chapters.

    Args:
        chapter_number: 1-based chapter index.
        chapter_title: Title of the chapter.
        chapter_content: The full text of the generated chapter.

    Returns:
        Fully rendered user prompt string.
    """
    return f"""The following is Chapter {chapter_number}: "{chapter_title}" from a book.

## Chapter Content
{chapter_content[:4000]}  

## Task
Write a concise 100-word summary of this chapter. The summary should capture:
- The main topic or argument
- Key points covered
- The narrative arc or progression
- Any critical facts, figures, or concepts introduced

The summary will be used as context for writing subsequent chapters, so accuracy
and coverage of key ideas is more important than stylistic polish.

Return ONLY the summary text — no labels or preamble.
"""


# ---------------------------------------------------------------------------
# Chapter revision prompt
# ---------------------------------------------------------------------------

def build_chapter_revision_prompt(
    book_title: str,
    chapter_number: int,
    chapter_title: str,
    current_content: str,
    reviewer_notes: str,
) -> str:
    """
    Build the prompt for revising a chapter based on reviewer feedback.

    Args:
        book_title: Parent book title.
        chapter_number: 1-based chapter index.
        chapter_title: Chapter title.
        current_content: The existing chapter prose.
        reviewer_notes: Human reviewer's revision requests.

    Returns:
        Fully rendered user prompt string.
    """
    return f"""You previously wrote Chapter {chapter_number}: "{chapter_title}" for the book "{book_title}".
A human reviewer has provided feedback.

{current_content}

{reviewer_notes.strip()}

## Task
Revise the chapter to fully address the reviewer's feedback. Maintain the overall
structure and length (1,500–3,000 words). Return ONLY the revised chapter content.
"""