# kindle-email

A self-hosted Python app that watches a Gmail label and delivers newsletters to your Kindle.

Forward a newsletter to yourself, apply the `SendToKindle` label in Gmail, and it appears on your Kindle as a clean, readable EPUB within minutes — no ads, no tracking pixels, no unsubscribe footers.

Inspired by [readbetter.io](https://readbetter.io/), built to run on your own infrastructure.

---

## How it works

1. You forward a newsletter to yourself in Gmail and apply the `SendToKindle` label
2. The app polls Gmail via IMAP every 5 minutes (configurable)
3. Each labelled email is parsed, cleaned with [readability-lxml](https://github.com/buriy/python-readability), and converted to EPUB
4. The EPUB is sent to your Kindle via Amazon's Send-to-Kindle email service
5. The email is moved to `SendToKindle/Processed` (or `SendToKindle/Failed` on error)

---

## Prerequisites

- A Gmail account with **2-Step Verification** enabled
- A **Gmail App Password** (Google Account → Security → 2-Step Verification → App passwords)
- Your **Kindle email address** (Kindle Settings → Your Account → Send-to-Kindle Email)
- Your Gmail address added to your Kindle's **Approved Personal Document Email List** (Amazon account → Manage Your Content and Devices → Preferences → Personal Document Settings)

---

## Setup

### 1. Create Gmail labels

In Gmail, create the label `SendToKindle`. The app will auto-create `SendToKindle/Processed` and `SendToKindle/Failed` on first run.

### 2. Configure

```bash
cp config.example.toml config.toml
chmod 600 config.toml
```

Edit `config.toml`:

```toml
[imap]
host = "imap.gmail.com"
port = 993
username = "you@gmail.com"
password = "your-gmail-app-password"

[smtp]
host = "smtp.gmail.com"
port = 587
username = "you@gmail.com"
password = "your-gmail-app-password"

[kindle]
address = "your_name@kindle.com"
from_address = "you@gmail.com"
```

**Never commit `config.toml`** — it's in `.gitignore`.

### 3. Run

**With Docker (recommended):**

```bash
docker compose up -d
```

**Without Docker:**

```bash
pip install .
python -m kindle_email
```

**Single pass (useful for cron):**

```bash
python -m kindle_email --once
```

---

## Configuration reference

| Setting | Default | Description |
|---|---|---|
| `imap.host` | `imap.gmail.com` | IMAP server |
| `imap.port` | `993` | IMAP port (SSL) |
| `smtp.host` | `smtp.gmail.com` | SMTP server |
| `smtp.port` | `587` | SMTP port (STARTTLS) |
| `labels.watch` | `SendToKindle` | Gmail label to poll |
| `labels.processed` | `SendToKindle/Processed` | Label for completed emails |
| `labels.failed` | `SendToKindle/Failed` | Label for failed emails |
| `schedule.poll_interval_seconds` | `300` | How often to check (seconds) |
| `processing.max_image_size_kb` | `500` | Max size per image to download |
| `processing.max_images_per_email` | `20` | Max images per email |
| `processing.download_external_images` | `true` | Download images from external URLs |
| `processing.image_timeout_seconds` | `10` | Timeout per image download |

Passwords can alternatively be set via environment variables `KINDLE_EMAIL_IMAP_PASSWORD` and `KINDLE_EMAIL_SMTP_PASSWORD` (useful for Docker secrets or CI). Do **not** pass them on the command line — that leaks to shell history.

---

## Project structure

```
src/kindle_email/
├── __main__.py   # Entry point
├── config.py     # Config loading
├── fetcher.py    # IMAP label scanning
├── parser.py     # MIME parsing
├── cleaner.py    # Content extraction and cleanup
├── epub.py       # EPUB generation
├── sender.py     # SMTP delivery
└── pipeline.py   # Orchestration
```

---

## Security

- External image downloads are protected against SSRF (private IPs, link-local addresses, and cloud metadata endpoints are blocked)
- HTML is sanitised with an attribute allowlist before EPUB generation
- Filenames derived from email subjects are sanitised to prevent path traversal and header injection
- IMAP and SMTP connections use SSL/STARTTLS with 30-second timeouts
- `config.toml` should be `chmod 600` — passwords in the config file are safer than on the command line

---

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

Tests use `.eml` fixture files in `tests/fixtures/`, including an SSRF test case.
