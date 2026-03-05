"""
main.py
=======
Entry point for the Automated Book Generation System.

Usage:
  python main.py               
  python main.py --daemon      
  python main.py --input-only  
  python main.py --book-id <UUID>  

The orchestrator handles all resumption logic — re-running main.py after
a crash will automatically continue from where the system left off.
"""

import argparse
import logging
import sys

from src.config import load_config, setup_logging
from src.orchestrator import Orchestrator


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns:
        argparse.Namespace with parsed flags.
    """
    parser = argparse.ArgumentParser(
        prog="book-gen",
        description="Automated Book Generation System — FSM-driven LLM pipeline",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in continuous polling mode (default: single tick)",
    )
    parser.add_argument(
        "--input-only",
        action="store_true",
        help="Only run Stage 1: read Excel and seed Supabase, then exit",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        metavar="SECONDS",
        help="Seconds between ticks in daemon mode (default: 30)",
    )
    parser.add_argument(
        "--book-id",
        type=str,
        default=None,
        metavar="UUID",
        help="Process only the book with this UUID (single tick)",
    )
    return parser.parse_args()


def main() -> int:
    """
    Application entry point.

    Returns:
        int: Exit code (0 = success, 1 = fatal error).
    """
    args = parse_args()

    # Bootstrap logging before anything else
    try:
        # Temporarily use defaults for log_dir before config is loaded
        setup_logging("logs")
        config = load_config()
        # Re-init logging with the actual configured log_dir
        setup_logging(str(config.log_dir))
    except EnvironmentError as exc:
        # Config load failed — print to stderr and exit
        print(f"[FATAL] Configuration error: {exc}", file=sys.stderr)
        print("Copy .env.example to .env and fill in all required values.", file=sys.stderr)
        return 1

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Automated Book Generation System — starting up")
    logger.info("=" * 60)

    try:
        orchestrator = Orchestrator(config)

        if args.input_only:
            logger.info("Mode: input-only — seeding from Excel and exiting")
            books = orchestrator.run_input_stage()
            logger.info("Seeded %d book(s). Exiting.", len(books))
            return 0

        if args.book_id:
            # Process a specific book by first running the input stage to
            # ensure it exists, then manually triggering one tick.
            logger.info("Mode: single-book — processing book_id=%s", args.book_id)
            book = orchestrator._db.get_book(args.book_id)
            if not book:
                logger.error("Book with id=%s not found in Supabase.", args.book_id)
                return 1
            orchestrator._process_book(book)
            return 0

        if args.daemon:
            logger.info("Mode: daemon — poll interval=%ds", args.poll_interval)
            # Run input stage once at startup to seed any new Excel rows
            orchestrator.run_input_stage()
            orchestrator.run_daemon(poll_interval=args.poll_interval)
        else:
            logger.info("Mode: one-shot — single orchestrator tick")
            orchestrator.run_input_stage()
            orchestrator.run_once()

        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user — shutting down cleanly")
        return 0
    except Exception as exc:
        logger.exception("Unhandled fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
