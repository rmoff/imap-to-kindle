"""Deliver a rendered EPUB/PDF to one or more targets.

Targets:
- ``smtp`` — Amazon Send-to-Kindle, via :mod:`kindle_email.sender`.
- ``cwa`` — drop the file into Calibre-Web-Automated's ingest folder, where it is
  auto-imported into the Calibre library and served to KOReader over OPDS.

Both are dispatched by :func:`deliver` according to ``config.delivery.targets``.
"""

from __future__ import annotations

import logging
import os
import tempfile

from . import sender
from .config import Config

log = logging.getLogger(__name__)


def _unique_path(directory: str, filename: str) -> str:
    """A path inside *directory* that does not already exist, based on *filename*.

    Directory components are stripped from *filename* so a hostile or accidental
    title can never write outside the ingest folder.
    """
    base = os.path.basename(filename) or "document.epub"
    dest = os.path.join(directory, base)
    if not os.path.exists(dest):
        return dest
    stem, ext = os.path.splitext(base)
    i = 1
    while True:
        candidate = os.path.join(directory, f"{stem}-{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1


def deliver_to_cwa(filename: str, data: bytes, config: Config) -> bool:
    """Write *data* into CWA's ingest folder atomically.

    The bytes are written to a temp file first, then ``os.replace``d into the ingest
    dir, so CWA's folder watcher never observes a half-written EPUB. When a separate
    ``staging_dir`` is configured (an unwatched dir on the same filesystem), the
    watcher never sees the temp file at all. Returns True on success, False on error.
    """
    cwa = config.cwa
    if cwa is None:
        log.error("CWA delivery requested but [cwa] config is missing")
        return False

    ingest_dir = cwa.ingest_dir
    staging_dir = cwa.staging_dir or ingest_dir
    try:
        os.makedirs(ingest_dir, exist_ok=True)
        if staging_dir != ingest_dir:
            os.makedirs(staging_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=staging_dir, prefix=".cwa-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            dest = _unique_path(ingest_dir, filename)
            os.replace(tmp, dest)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as e:
        log.error("Failed to write '%s' to CWA ingest dir %s: %s", filename, ingest_dir, e)
        return False

    log.info("Delivered '%s' to CWA ingest dir %s", os.path.basename(dest), ingest_dir)
    return True


def deliver(filename: str, data: bytes, config: Config) -> bool:
    """Deliver to every configured target. True only if all targets succeed.

    Every target is attempted regardless of earlier failures (no short-circuit), so a
    partial failure never silently drops one delivery — it just reports overall failure,
    which lets the caller retry.
    """
    results: list[bool] = []
    for target in config.delivery.targets:
        if target == "smtp":
            results.append(sender.send(filename, data, config))
        elif target == "cwa":
            results.append(deliver_to_cwa(filename, data, config))
        else:
            log.error("Unknown delivery target '%s'; skipping", target)
            results.append(False)
    return bool(results) and all(results)
