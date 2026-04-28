from __future__ import annotations

import argparse
import logging
import time

from node_api.logging import configure_logging
from node_api.services.translator_blocks_found import poll_blocks_found_once
from node_api.services.translator_blocks_found_store import TranslatorBlocksFoundStore
from node_api.settings import get_settings

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll translator miner-work snapshot and persist block-found counter deltas."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one polling pass and exit.",
    )
    parser.add_argument(
        "--interval-secs",
        type=int,
        default=15,
        help="Polling interval in seconds for loop mode (default: 15).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.interval_secs < 1:
        raise SystemExit("--interval-secs must be >= 1")

    settings = get_settings()
    configure_logging(level=settings.log_level)
    store = TranslatorBlocksFoundStore.from_settings(settings)

    if args.once:
        try:
            stats = poll_blocks_found_once(settings, store)
        except Exception:
            logger.exception("Translator blocks-found poller pass failed")
            return 1
        logger.info("Translator blocks-found poller pass complete", extra=stats)
        return 0

    logger.info(
        "Starting translator blocks-found poller",
        extra={
            "interval_secs": args.interval_secs,
            "db_path": str(store.db_path),
        },
    )
    while True:
        try:
            stats = poll_blocks_found_once(settings, store)
            logger.info("Translator blocks-found poller pass complete", extra=stats)
        except Exception:
            logger.exception("Translator blocks-found poller pass failed")
        time.sleep(args.interval_secs)


if __name__ == "__main__":
    raise SystemExit(main())
