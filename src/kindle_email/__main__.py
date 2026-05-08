"""Entry point: python -m kindle_email [--once] [--config path/to/config.toml]"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import config as cfg
from . import raindrop_fetcher
from .fetcher import fetch_and_process
from .pipeline import process_email, process_raindrop


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

    def drain_sources() -> None:
        try:
            count = fetch_and_process(config, lambda raw: process_email(raw, config))
            if count:
                log.info("Processed %d email(s)", count)
        except Exception as e:
            log.error("IMAP poll error: %s", e)

        if config.raindrop is not None:
            try:
                count = raindrop_fetcher.fetch_and_process(
                    config, lambda r: process_raindrop(r, config)
                )
                if count:
                    log.info("Processed %d raindrop(s)", count)
            except Exception as e:
                log.error("Raindrop poll error: %s", e)

    if args.once:
        log.info("Running single pass")
        drain_sources()
        return

    sources = ["IMAP label " + config.labels.watch]
    if config.raindrop is not None:
        sources.append(f"Raindrop tag #{config.raindrop.source_tag}")
    log.info(
        "Starting poll loop (interval: %ds, sources: %s)",
        config.schedule.poll_interval_seconds,
        ", ".join(sources),
    )
    while True:
        drain_sources()
        time.sleep(config.schedule.poll_interval_seconds)


if __name__ == "__main__":
    main()
