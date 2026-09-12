"""Load and validate configuration from config.toml with env var overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ImapConfig:
    host: str
    port: int
    username: str
    password: str


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str


@dataclass
class KindleConfig:
    address: str
    from_address: str


@dataclass
class LabelConfig:
    watch: str
    processed: str
    failed: str


@dataclass
class ScheduleConfig:
    poll_interval_seconds: int


@dataclass
class ProcessingConfig:
    max_image_size_kb: int
    max_images_per_email: int
    download_external_images: bool
    image_timeout_seconds: int
    # Local FlareSolverr endpoint (e.g. "http://host.docker.internal:8191") used
    # as a last-resort fetch fallback for Cloudflare-walled pages; None = off.
    flaresolverr_url: str | None = None


@dataclass
class RaindropConfig:
    token: str
    source_tag: str = "sendtokindle"
    processed_tag: str = "sendtokindle_processed"
    failed_tag: str = "sendtokindle_failed"


@dataclass
class DiscordConfig:
    webhook_url: str


@dataclass
class CwaConfig:
    # Folder CWA watches for auto-import (container path). EPUBs are dropped here.
    ingest_dir: str
    # Optional unwatched scratch dir on the SAME filesystem as ingest_dir. When set,
    # the EPUB is written here first and atomically renamed into ingest_dir, so CWA's
    # watcher never sees a partial file. Falls back to a dotfile temp inside ingest_dir.
    staging_dir: str | None = None


@dataclass
class DeliveryConfig:
    # Ordered, de-duplicated subset of {"smtp", "cwa"}. Each produced EPUB is
    # delivered to every listed target; delivery counts as successful only if
    # all targets succeed.
    targets: list[str]


@dataclass
class Config:
    imap: ImapConfig
    labels: LabelConfig
    schedule: ScheduleConfig
    processing: ProcessingConfig
    # Defaults to Amazon Send-to-Kindle (the app's original, only behaviour) when a
    # Config is built without an explicit target; load() always sets this from config.
    delivery: DeliveryConfig = field(default_factory=lambda: DeliveryConfig(targets=["smtp"]))
    smtp: SmtpConfig | None = None
    kindle: KindleConfig | None = None
    cwa: CwaConfig | None = None
    raindrop: RaindropConfig | None = None
    discord: DiscordConfig | None = None


_ALLOWED_TARGETS = ("smtp", "cwa")


def _parse_targets(delivery_section: dict) -> list[str]:
    """Parse and validate [delivery] targets. Accepts a string or a list; defaults to smtp."""
    raw_targets = delivery_section.get("targets", delivery_section.get("target", ["smtp"]))
    if isinstance(raw_targets, str):
        raw_targets = [raw_targets]
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("delivery.targets must be a non-empty list (or a single string).")
    targets: list[str] = []
    for item in raw_targets:
        name = str(item).strip().lower()
        if name not in _ALLOWED_TARGETS:
            raise ValueError(
                f"Unknown delivery target '{name}'. Allowed: {', '.join(_ALLOWED_TARGETS)}."
            )
        if name not in targets:
            targets.append(name)
    return targets


def load(path: str | Path = "config.toml") -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    imap_section = raw["imap"]
    imap_password = os.environ.get("KINDLE_EMAIL_IMAP_PASSWORD") or imap_section.get("password", "")
    if not imap_password:
        raise ValueError("IMAP password not set. Use KINDLE_EMAIL_IMAP_PASSWORD env var or imap.password in config.")

    targets = _parse_targets(raw.get("delivery", {}))

    # SMTP + Kindle are only needed when delivering via Amazon Send-to-Kindle.
    smtp_config: SmtpConfig | None = None
    kindle_config: KindleConfig | None = None
    if "smtp" in targets:
        smtp_section = raw.get("smtp")
        if smtp_section is None:
            raise ValueError("Delivery target 'smtp' requires an [smtp] section.")
        smtp_password = os.environ.get("KINDLE_EMAIL_SMTP_PASSWORD") or smtp_section.get("password", "")
        if not smtp_password:
            raise ValueError("SMTP password not set. Use KINDLE_EMAIL_SMTP_PASSWORD env var or smtp.password in config.")
        smtp_config = SmtpConfig(
            host=smtp_section["host"],
            port=int(smtp_section.get("port", 587)),
            username=smtp_section["username"],
            password=smtp_password,
        )
        kindle_section = raw.get("kindle")
        if kindle_section is None:
            raise ValueError("Delivery target 'smtp' requires a [kindle] section.")
        kindle_config = KindleConfig(
            address=kindle_section["address"],
            from_address=kindle_section["from_address"],
        )

    # CWA ingest folder is only needed when delivering into the Calibre library.
    cwa_config: CwaConfig | None = None
    if "cwa" in targets:
        cwa_section = raw.get("cwa")
        if not cwa_section or not cwa_section.get("ingest_dir"):
            raise ValueError("Delivery target 'cwa' requires a [cwa] section with ingest_dir.")
        cwa_config = CwaConfig(
            ingest_dir=cwa_section["ingest_dir"],
            staging_dir=cwa_section.get("staging_dir") or None,
        )

    labels = raw.get("labels", {})
    schedule = raw.get("schedule", {})
    processing = raw.get("processing", {})
    raindrop_section = raw.get("raindrop")
    discord_section = raw.get("discord")

    discord_config: DiscordConfig | None = None
    discord_webhook = os.environ.get("KINDLE_EMAIL_DISCORD_WEBHOOK") or (
        (discord_section or {}).get("webhook_url", "")
    )
    if discord_webhook:
        discord_config = DiscordConfig(webhook_url=discord_webhook)

    raindrop_config: RaindropConfig | None = None
    if raindrop_section and raindrop_section.get("enabled", False):
        token = os.environ.get("KINDLE_EMAIL_RAINDROP_TOKEN") or raindrop_section.get("token", "")
        if not token:
            raise ValueError(
                "Raindrop is enabled but no token set. Use KINDLE_EMAIL_RAINDROP_TOKEN env var or raindrop.token in config."
            )
        # Raindrop tag names are stored without a leading '#', so strip one if present.
        def _tag(key: str, default: str) -> str:
            return str(raindrop_section.get(key, default)).lstrip("#")
        raindrop_config = RaindropConfig(
            token=token,
            source_tag=_tag("source_tag", "sendtokindle"),
            processed_tag=_tag("processed_tag", "sendtokindle_processed"),
            failed_tag=_tag("failed_tag", "sendtokindle_failed"),
        )

    return Config(
        imap=ImapConfig(
            host=imap_section["host"],
            port=int(imap_section.get("port", 993)),
            username=imap_section["username"],
            password=imap_password,
        ),
        delivery=DeliveryConfig(targets=targets),
        labels=LabelConfig(
            watch=labels.get("watch", "SendToKindle"),
            processed=labels.get("processed", "SendToKindle/Processed"),
            failed=labels.get("failed", "SendToKindle/Failed"),
        ),
        schedule=ScheduleConfig(
            poll_interval_seconds=int(schedule.get("poll_interval_seconds", 300)),
        ),
        processing=ProcessingConfig(
            max_image_size_kb=int(processing.get("max_image_size_kb", 500)),
            max_images_per_email=int(processing.get("max_images_per_email", 20)),
            download_external_images=bool(processing.get("download_external_images", True)),
            image_timeout_seconds=int(processing.get("image_timeout_seconds", 10)),
            flaresolverr_url=processing.get("flaresolverr_url") or None,
        ),
        smtp=smtp_config,
        kindle=kindle_config,
        cwa=cwa_config,
        raindrop=raindrop_config,
        discord=discord_config,
    )
