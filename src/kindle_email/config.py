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
class Config:
    imap: ImapConfig
    smtp: SmtpConfig
    kindle: KindleConfig
    labels: LabelConfig
    schedule: ScheduleConfig
    processing: ProcessingConfig


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
    )
