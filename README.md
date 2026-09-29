# imap-to-kindle

A self-hosted Python app that delivers newsletters and web pages to your Kindle.

Two ways in:

- **Forward a newsletter** to yourself and move it to a watched IMAP folder. It appears on your Kindle as a clean, readable EPUB within minutes — no ads, no tracking pixels, no unsubscribe footers.
- **Tag a Raindrop.io bookmark `#sendtokindle`** and the app fetches the page, cleans it, and delivers it — handy for the mobile share sheet.

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

Copy `docker-compose.example.yml` into a deployment directory as `docker-compose.yml`,
next to your `config.toml` and `.env`, adjust the image/volumes, then run:

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
| `processing.pdf_relayout` | `true` | Restack two-column PDFs for the e-reader screen (see [PDFs](#pdfs)) |
| `raindrop.enabled` | `false` | Turn on Raindrop polling |
| `raindrop.token` | | Raindrop test token (or `KINDLE_EMAIL_RAINDROP_TOKEN` env var) |
| `raindrop.source_tag` | `sendtokindle` | Tag that marks a bookmark for delivery |
| `raindrop.processed_tag` | `sendtokindle_processed` | Tag added (alongside the source tag) after successful delivery |
| `raindrop.failed_tag` | `sendtokindle_failed` | Tag added (alongside the source tag) when delivery fails |

Passwords/tokens can alternatively be set via environment variables `KINDLE_EMAIL_IMAP_PASSWORD`, `KINDLE_EMAIL_SMTP_PASSWORD`, and `KINDLE_EMAIL_RAINDROP_TOKEN` (useful for Docker). Do **not** pass them on the command line — that leaks to shell history.

---

## Raindrop mode

Tag any [Raindrop.io](https://raindrop.io) bookmark `#sendtokindle` and the app will fetch the page, clean it, and send it to your Kindle on the next poll. After delivery `#sendtokindle_processed` (or `#sendtokindle_failed` on failure) is added alongside it so nothing gets re-sent. `#sendtokindle` itself stays on the bookmark, so Raindrop keeps offering it as a tag suggestion. To retry a failure, remove `#sendtokindle_failed`; to re-send, remove `#sendtokindle_processed`.

### Setup

1. Create a test token at https://app.raindrop.io/settings/integrations.
2. Add a `[raindrop]` section to `config.toml` (see `config.example.toml`):

```toml
[raindrop]
enabled = true
token = "your-token"
```

3. On your phone: share a URL to Raindrop and add the `sendtokindle` tag. Next poll it lands on your Kindle.

### PDFs

A bookmark that points at a PDF (Raindrop marks these as documents, or the URL ends in `.pdf`, or it's an `arxiv.org/pdf/…` link) is delivered as a PDF rather than rendered as a web page. It's named after the paper's title, read from the PDF itself.

Two-column PDFs, the usual academic-paper layout, are unreadable at full-page size on a 6–7" screen. So each column is cropped out and restacked onto pages shaped like a Kindle screen (3:4) and as wide as one column. Text shows at roughly print size with no zooming or panning. Full-width parts (title block, wide figures and tables) are kept whole and scaled to fit. Page breaks fall between lines, never through one. The text stays real text rather than an image, so it's still searchable and highlightable.

Anything that isn't clearly two-column is sent unchanged. The same applies to PDFs attached to emails. Set `processing.pdf_relayout = false` to always send PDFs as-is.

---

## Project structure

```
src/kindle_email/
├── __main__.py          # Entry point
├── config.py            # Config loading
├── fetcher.py           # IMAP folder scanning
├── raindrop_fetcher.py  # Raindrop.io API client
├── parser.py            # MIME parsing
├── cleaner.py           # Content extraction and cleanup
├── url_fetcher.py       # Fetch a URL and render it as cleaned HTML (or fetch a PDF)
├── pdf_layout.py        # Restack two-column PDFs for an e-reader screen
├── epub.py              # EPUB generation
├── sender.py            # SMTP delivery
└── pipeline.py          # Orchestration
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
