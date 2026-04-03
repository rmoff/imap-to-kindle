# imap-to-kindle

A self-hosted Python app that watches an IMAP folder and delivers newsletters to your Kindle.

Forward a newsletter to yourself, move it to a watched folder, and it appears on your Kindle as a clean, readable EPUB within minutes — no ads, no tracking pixels, no unsubscribe footers.

Inspired by [readbetter.io](https://readbetter.io/), built to run on your own infrastructure.

> **Note:** This project was written entirely by [Claude](https://claude.ai/) (Anthropic's AI). It works, but use it with that context in mind.

---

## How it works

1. An email arrives in your watched IMAP folder (e.g. you forward a newsletter and label/move it)
2. The app polls via IMAP every 5 minutes (configurable)
3. Each email is parsed, cleaned with [readability-lxml](https://github.com/buriy/python-readability), and converted to EPUB
4. The EPUB is sent to your Kindle via Amazon's Send-to-Kindle email service
5. The email is moved to a processed folder (or a failed folder on error)

---

## Prerequisites

- An email account accessible via IMAP
- An SMTP server for outbound delivery (can be the same account)
- Your **Kindle email address** (Kindle Settings → Your Account → Send-to-Kindle Email)
- Your sending address added to your Kindle's **Approved Personal Document Email List** (Amazon account → Manage Your Content and Devices → Preferences → Personal Document Settings)

---

## Setup

### 1. Create the watched folder

Create an IMAP folder/label called `SendToKindle` in your mail client. The app will auto-create `SendToKindle/Processed` and `SendToKindle/Failed` on first run.

**Gmail users:** Create a label called `SendToKindle`. Use an [App Password](https://support.google.com/accounts/answer/185833) (requires 2-Step Verification) rather than your main password.

### 2. Configure

```bash
cp config.example.toml config.toml
chmod 600 config.toml
```

Edit `config.toml`:

```toml
[imap]
host = "imap.example.com"   # e.g. imap.gmail.com
port = 993
username = "you@example.com"
password = "your-password"

[smtp]
host = "smtp.example.com"   # e.g. smtp.gmail.com
port = 587
username = "you@example.com"
password = "your-password"

[kindle]
address = "your_name@kindle.com"
from_address = "you@example.com"
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
| `imap.host` | | IMAP server hostname |
| `imap.port` | `993` | IMAP port (SSL) |
| `smtp.host` | | SMTP server hostname |
| `smtp.port` | `587` | SMTP port (STARTTLS) |
| `labels.watch` | `SendToKindle` | IMAP folder to poll |
| `labels.processed` | `SendToKindle/Processed` | Folder for completed emails |
| `labels.failed` | `SendToKindle/Failed` | Folder for failed emails |
| `schedule.poll_interval_seconds` | `300` | How often to check (seconds) |
| `processing.max_image_size_kb` | `500` | Max size per image to download |
| `processing.max_images_per_email` | `20` | Max images per email |
| `processing.download_external_images` | `true` | Download images from external URLs |
| `processing.image_timeout_seconds` | `10` | Timeout per image download |

Passwords can alternatively be set via environment variables `KINDLE_EMAIL_IMAP_PASSWORD` and `KINDLE_EMAIL_SMTP_PASSWORD` (useful for Docker). Do **not** pass them on the command line — that leaks to shell history.

---

## Project structure

```
src/kindle_email/
├── __main__.py   # Entry point
├── config.py     # Config loading
├── fetcher.py    # IMAP folder scanning
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
