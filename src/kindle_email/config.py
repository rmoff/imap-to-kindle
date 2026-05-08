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
class Config:
    imap: ImapConfig
    smtp: SmtpConfig
    kindle: KindleConfig
    labels: LabelConfig
    schedule: ScheduleConfig
    processing: ProcessingConfig
    raindrop: RaindropConfig | None = None
    discord: DiscordConfig | None = None


def load(path: str | Path = "config.toml") -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    imap_section = raw["imap"]
    smtp_section = raw["smtp"]

    imap_password = os.environ.get("KINDLE_EMAIL_IMAP_PASSWORD") or imap_section.get("password", "")
    smtp_password = os.environ.get("KINDLE_EMAIL_SMTP_PASSWORD") or smtp_section.get("password", "")

    if not imap_password:
        raise ValueError("IMAP password not set. Use KINDLE_EMAIL_IMAP_PASSWORD env var or imap.password in config.")
    if not smtp_password:
        raise ValueError("SMTP password not set. Use KINDLE_EMAIL_SMTP_PASSWORD env var or smtp.password in config.")

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
        smtp=SmtpConfig(
            host=smtp_section["host"],
            port=int(smtp_section.get("port", 587)),
            username=smtp_section["username"],
            password=smtp_password,
        ),
        kindle=KindleConfig(
            address=raw["kindle"]["address"],
            from_address=raw["kindle"]["from_address"],
        ),
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
        ),
        raindrop=raindrop_config,
        discord=discord_config,
    )
