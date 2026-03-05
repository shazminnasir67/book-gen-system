"""
src/config.py
=============
Central configuration module for the Automated Book Generation System.

Loads all environment variables from the .env file via python-dotenv and
exposes them as typed attributes on the `Config` dataclass. This is the
single source of truth for all external credentials and tuneable parameters.
Every other module imports from here — never directly from os.environ.
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _require(key: str) -> str:
    """Return the value of a required environment variable or raise an error."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Please copy .env.example to .env and fill in all values."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    """Return the value of an optional environment variable with a default."""
    return os.getenv(key, default)


def setup_logging(log_dir: str = "logs") -> None:
    """
    Configure the root logger to write to both the console and a log file.

    Args:
        log_dir: Directory in which `system.log` will be created.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(Path(log_dir) / "system.log", encoding="utf-8"),
        ],
    )


@dataclass(frozen=True)
class Config:
    """
    Immutable application configuration loaded from environment variables.

    Attributes are grouped by subsystem. The class is frozen so that no
    module can accidentally mutate shared config at runtime.
    """

    # --- Supabase ---
    supabase_url: str
    supabase_service_key: str

    # --- Google Gemini ---
    gemini_api_key: str
    gemini_model: str

    # --- Email / SMTP ---
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    notification_from: str
    notification_to: str

    # --- Microsoft Teams ---
    teams_webhook_url: str

    # --- File Paths ---
    input_excel_path: Path
    output_dir: Path
    log_dir: Path

    # --- LLM Tunables ---
    llm_max_tokens: int
    llm_temperature: float
    llm_max_retries: int
    llm_retry_wait_seconds: int


def load_config() -> Config:
    """
    Instantiate and return the application Config from environment variables.

    Raises:
        EnvironmentError: If any required variable is missing.

    Returns:
        Config: Fully populated, immutable Config instance.
    """
    return Config(
        # Supabase
        supabase_url=_require("SUPABASE_URL"),
        supabase_service_key=_require("SUPABASE_SERVICE_KEY"),

        # Gemini
        gemini_api_key=_require("GEMINI_API_KEY"),
        gemini_model=_optional("GEMINI_MODEL", "gemini-1.5-pro"),

        # SMTP
        smtp_host=_optional("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=int(_optional("SMTP_PORT", "587")),
        smtp_username=_require("SMTP_USERNAME"),
        smtp_password=_require("SMTP_PASSWORD"),
        notification_from=_require("NOTIFICATION_FROM"),
        notification_to=_require("NOTIFICATION_TO"),

        # Teams
        teams_webhook_url=_optional("TEAMS_WEBHOOK_URL", ""),

        # Paths
        input_excel_path=Path(_optional("INPUT_EXCEL_PATH", "src/input/books_input.xlsx")),
        output_dir=Path(_optional("OUTPUT_DIR", "output")),
        log_dir=Path(_optional("LOG_DIR", "logs")),

        # LLM
        llm_max_tokens=int(_optional("LLM_MAX_TOKENS", "8192")),
        llm_temperature=float(_optional("LLM_TEMPERATURE", "1.0")),
        llm_max_retries=int(_optional("LLM_MAX_RETRIES", "3")),
        llm_retry_wait_seconds=int(_optional("LLM_RETRY_WAIT_SECONDS", "10")),
    )
