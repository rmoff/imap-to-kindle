"""Deliver a rendered EPUB/PDF to one or more targets.

Targets:
- ``smtp`` — Amazon Send-to-Kindle, via :mod:`kindle_email.sender`.
- ``folder`` — write the file into a directory (e.g. a Syncthing folder that pushes it
  to the Kindle, or an auto-import folder another tool watches).

Both are dispatched by :func:`deliver` according to ``config.delivery.targets``.
"""

from __future__ import annotations

import errno
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


def _atomic_write(data: bytes, temp_dir: str, dest: str) -> None:
    """Write *data* to a temp file in *temp_dir*, then atomically rename onto *dest*.

    Raises OSError (e.g. EXDEV if temp_dir and dest are on different filesystems).
    """
    fd, tmp = tempfile.mkstemp(dir=temp_dir, prefix=".folder-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def deliver_to_folder(filename: str, data: bytes, config: Config) -> bool:
    """Write *data* into the configured folder atomically.

    The bytes are written to a temp file first, then ``os.replace``d into the folder,
    so a watcher (Syncthing, an importer) never observes a half-written EPUB. A configured
    ``staging_dir`` keeps the temp file out of the watched folder entirely — but only
    if it is on the same filesystem; in Docker, separate bind mounts are separate
    devices, so if the rename would cross a device boundary (EXDEV) we transparently
    fall back to staging inside the ingest dir. Returns True on success, False on error.
    """
    folder = config.folder
    if folder is None:
        log.error("Folder delivery requested but [folder] config is missing")
        return False

    ingest_dir = folder.dir
    staging_dir = folder.staging_dir or ingest_dir
    try:
        os.makedirs(ingest_dir, exist_ok=True)
        dest = _unique_path(ingest_dir, filename)
        if staging_dir != ingest_dir:
            os.makedirs(staging_dir, exist_ok=True)
            try:
                _atomic_write(data, staging_dir, dest)
            except OSError as e:
                if e.errno != errno.EXDEV:
                    raise
                log.warning(
                    "Staging dir %s is on a different filesystem than ingest dir %s; "
                    "writing via the ingest dir instead", staging_dir, ingest_dir,
                )
                _atomic_write(data, ingest_dir, dest)
        else:
            _atomic_write(data, ingest_dir, dest)
    except OSError as e:
        log.error("Failed to write '%s' to folder %s: %s", filename, ingest_dir, e)
        return False

    log.info("Delivered '%s' to folder %s", os.path.basename(dest), ingest_dir)
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
        elif target == "folder":
            results.append(deliver_to_folder(filename, data, config))
        else:
            log.error("Unknown delivery target '%s'; skipping", target)
            results.append(False)
    return bool(results) and all(results)
