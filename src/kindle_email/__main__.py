"""Entry point: python -m kindle_email [--once] [--config path/to/config.toml]"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import config as cfg
from .fetcher import fetch_and_process
from .pipeline import process_email


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watch a Gmail label and deliver newsletters to your Kindle."
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: config.toml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process once and exit (useful for cron deployment)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from config",
    )
    args = parser.parse_args()

    try:
        config = cfg.load(args.config)
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    log_level = args.log_level or "INFO"
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger(__name__)

    if args.once:
        log.info("Running single pass")
        count = fetch_and_process(config, lambda raw: process_email(raw, config))
        log.info("Processed %d email(s)", count)
        return

    log.info(
        "Starting poll loop (interval: %ds, watching label: %s)",
        config.schedule.poll_interval_seconds,
        config.labels.watch,
    )
    while True:
        try:
            count = fetch_and_process(config, lambda raw: process_email(raw, config))
            if count:
                log.info("Processed %d email(s)", count)
        except Exception as e:
            log.error("Poll loop error: %s", e)

        time.sleep(config.schedule.poll_interval_seconds)


if __name__ == "__main__":
    main()
