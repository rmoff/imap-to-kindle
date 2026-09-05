"""Tests for url_fetcher.py — page fetching and cleaned-content merging."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kindle_email import url_fetcher
from kindle_email.config import ProcessingConfig

_FIXTURES = Path(__file__).parent / "fixtures"


def _config(**overrides) -> ProcessingConfig:
    defaults = dict(
        max_image_size_kb=500,
        max_images_per_email=20,
        download_external_images=False,  # keep tests offline
        image_timeout_seconds=10,
    )
    defaults.update(overrides)
    return ProcessingConfig(**defaults)


def _article_body(marker: str) -> str:
    """A body with enough real text to clear the substantive-article threshold."""
    return (
        f"<article><p>{marker} "
        + "This is genuine article prose that goes on for several sentences so "
        "that the extractor sees a real post rather than a redirect stub. " * 3
        + "</p></article>"
    )


def test_build_cleaned_content_merges_pages():
    page1 = (
        "<html><head><title>Page One</title></head>"
        f"<body>{_article_body('Article one content here.')}</body></html>"
    )
    page2 = (
        "<html><head><title>Page Two</title></head>"
        f"<body>{_article_body('Article two content here.')}</body></html>"
    )

    with patch.object(url_fetcher, "fetch_page", side_effect=[page1, page2]):
        cleaned = url_fetcher.build_cleaned_content(
            ["https://a.com/1", "https://b.com/2"], _config()
        )

    assert cleaned is not None
    assert "Article one content" in cleaned.html
    assert "Article two content" in cleaned.html
    assert "Page One" in cleaned.html
    assert "Page Two" in cleaned.html
    assert cleaned.title.startswith("Page One")
    assert "+1 more" in cleaned.title


def test_build_cleaned_content_single_url_uses_page_title():
    page = (
        "<html><head><title>Just One</title></head>"
        f"<body>{_article_body('Some text.')}</body></html>"
    )
    with patch.object(url_fetcher, "fetch_page", return_value=page):
        cleaned = url_fetcher.build_cleaned_content(["https://a.com/1"], _config())
    assert cleaned is not None
    assert cleaned.title == "Just One"


def test_build_cleaned_content_returns_none_when_all_fail():
    with patch.object(url_fetcher, "fetch_page", return_value=None):
        cleaned = url_fetcher.build_cleaned_content(["https://a.com/1"], _config())
    assert cleaned is None


def test_build_cleaned_content_skips_failed_url():
    """If one URL fails but another succeeds, deliver what we have."""
    page = (
        "<html><head><title>Survivor</title></head>"
        f"<body>{_article_body('Content.')}</body></html>"
    )
    with patch.object(url_fetcher, "fetch_page", side_effect=[None, page]):
        cleaned = url_fetcher.build_cleaned_content(
            ["https://a.com/broken", "https://b.com/ok"], _config()
        )
    assert cleaned is not None
    assert "Survivor" in cleaned.html


# A Substack-style redirect interstitial: the <title> is the destination URL
# and the body is just a link — no article text. This is what Substack
# intermittently serves for open.substack.com share links instead of the post.
_INTERSTITIAL = (
    "<html><head><title>"
    "https://charitydotwtf.substack.com/p/ai-enthusiasts?triedRedirect=true"
    "</title></head><body>"
    '<a href="https://charitydotwtf.substack.com/p/ai-enthusiasts">'
    "https://charitydotwtf.substack.com/p/ai-enthusiasts</a>"
    "</body></html>"
)

_REAL_ARTICLE = (
    "<html><head><title>AI enthusiasts are in a race against time</title></head>"
    "<body><article>"
    + "<p>Both sides are grappling with a real existential threat and screaming "
    "into the void. Here is how to close the gap and get everyone pulling in the "
    "same direction, paragraph after paragraph of genuine article prose.</p>" * 5
    + "</article></body></html>"
)


def test_build_cleaned_content_rejects_thin_interstitial():
    """A redirect stub with no article text must NOT be delivered as content.

    Both the initial fetch and the retry return the stub, so there is no real
    article to ship — build_cleaned_content should return None, which makes the
    pipeline send a Discord failure notification instead of a bare-URL epub.
    """
    with patch.object(url_fetcher, "fetch_page", side_effect=[_INTERSTITIAL, _INTERSTITIAL]):
        cleaned = url_fetcher.build_cleaned_content(
            ["https://open.substack.com/pub/charitydotwtf/p/ai-enthusiasts"], _config()
        )
    assert cleaned is None


def test_build_cleaned_content_retries_thin_then_succeeds():
    """If the first fetch yields a thin stub, a refetch that returns the real
    article should be used — this recovers Substack's intermittent interstitial."""
    with patch.object(url_fetcher, "fetch_page", side_effect=[_INTERSTITIAL, _REAL_ARTICLE]):
        cleaned = url_fetcher.build_cleaned_content(
            ["https://open.substack.com/pub/charitydotwtf/p/ai-enthusiasts"], _config()
        )
    assert cleaned is not None
    assert "existential threat" in cleaned.html
    assert cleaned.title.startswith("AI enthusiasts")


def test_resolve_client_redirect_reads_real_substack_stub():
    """The captured Substack interstitial must yield its real-article target."""
    stub = (_FIXTURES / "substack_interstitial.html").read_text()
    target = url_fetcher._resolve_client_redirect(stub, "https://open.substack.com/x")
    assert target is not None
    assert target.startswith("https://charitydotwtf.substack.com/p/ai-enthusiasts")
    assert "triedRedirect=true" in target


def test_resolve_client_redirect_handles_js_location_replace():
    html = '<html><head></head><body><script>location.replace("https://example.com/real")</script></body></html>'
    assert url_fetcher._resolve_client_redirect(html, "https://a.com") == "https://example.com/real"


def test_resolve_client_redirect_relative_meta_refresh():
    html = '<meta http-equiv="refresh" content="0; url=/article/123">'
    assert url_fetcher._resolve_client_redirect(html, "https://a.com/x") == "https://a.com/article/123"


def test_resolve_client_redirect_none_for_real_article():
    html = "<html><head><title>Real</title></head><body><article><p>Lots of text.</p></article></body></html>"
    assert url_fetcher._resolve_client_redirect(html, "https://a.com") is None


def test_fetch_page_follows_client_side_redirect():
    """fetch_page must transparently follow a JS/meta interstitial to the article."""
    stub = (_FIXTURES / "substack_interstitial.html").read_text()
    article = "<html><head><title>AI enthusiasts</title></head><body><article><p>Real prose.</p></article></body></html>"
    with patch.object(url_fetcher, "_direct_fetch", side_effect=[stub, article]), \
         patch.object(url_fetcher, "is_safe_url", return_value=True):
        result = url_fetcher.fetch_page("https://open.substack.com/pub/charitydotwtf/p/x")
    assert result == article


def test_fetch_page_blocks_ssrf():
    """fetch_page must refuse private/loopback URLs before opening a socket."""
    assert url_fetcher.fetch_page("http://127.0.0.1/") is None
    assert url_fetcher.fetch_page("http://169.254.169.254/latest/meta-data/") is None


# --- FlareSolverr fallback -------------------------------------------------
# When both the direct fetch and r.jina.ai are blocked (Cloudflare bot-walls,
# e.g. patreon.com), fetch_page falls back to a local FlareSolverr instance,
# which solves the challenge in a real browser and returns the rendered DOM.

import io
import json


class _FakeHttpResponse(io.BytesIO):
    """Minimal stand-in for urlopen's response: bytes + context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _flaresolverr_envelope(html="<html><body>solved</body></html>",
                           status="ok", solution_status=200) -> bytes:
    return json.dumps({
        "status": status,
        "message": "",
        "solution": {"status": solution_status, "url": "https://a.com/x", "response": html},
    }).encode()


def test_fetch_page_falls_back_to_flaresolverr_when_jina_fails():
    with patch.object(url_fetcher, "is_safe_url", return_value=True), \
         patch.object(url_fetcher, "_direct_fetch", return_value=None), \
         patch.object(url_fetcher, "_jina_fetch", return_value=None), \
         patch.object(url_fetcher, "_flaresolverr_fetch", return_value="<html>via fs</html>") as fs:
        result = url_fetcher.fetch_page(
            "https://blocked.example/post", flaresolverr_url="http://fs:8191"
        )
    assert result == "<html>via fs</html>"
    fs.assert_called_once_with("https://blocked.example/post", "http://fs:8191")


def test_fetch_page_skips_flaresolverr_when_not_configured():
    with patch.object(url_fetcher, "is_safe_url", return_value=True), \
         patch.object(url_fetcher, "_direct_fetch", return_value=None), \
         patch.object(url_fetcher, "_jina_fetch", return_value=None), \
         patch.object(url_fetcher, "_flaresolverr_fetch") as fs:
        result = url_fetcher.fetch_page("https://blocked.example/post")
    assert result is None
    fs.assert_not_called()


def test_flaresolverr_fetch_returns_solution_html():
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return _FakeHttpResponse(_flaresolverr_envelope("<html><body>rendered page</body></html>"))

    with patch.object(url_fetcher.urllib.request, "urlopen", fake_urlopen):
        result = url_fetcher._flaresolverr_fetch("https://a.com/x", "http://fs:8191")

    assert result == "<html><body>rendered page</body></html>"
    assert captured["url"] == "http://fs:8191/v1"
    assert captured["body"]["cmd"] == "request.get"
    assert captured["body"]["url"] == "https://a.com/x"


def test_flaresolverr_fetch_none_when_solver_reports_error():
    with patch.object(
        url_fetcher.urllib.request, "urlopen",
        lambda req, timeout: _FakeHttpResponse(
            _flaresolverr_envelope(status="error")
        ),
    ):
        assert url_fetcher._flaresolverr_fetch("https://a.com/x", "http://fs:8191") is None


def test_flaresolverr_fetch_none_when_target_still_blocked():
    with patch.object(
        url_fetcher.urllib.request, "urlopen",
        lambda req, timeout: _FakeHttpResponse(
            _flaresolverr_envelope(solution_status=403)
        ),
    ):
        assert url_fetcher._flaresolverr_fetch("https://a.com/x", "http://fs:8191") is None


def test_flaresolverr_fetch_none_when_unreachable():
    import urllib.error

    def raise_urlerror(req, timeout):
        raise urllib.error.URLError("connection refused")

    with patch.object(url_fetcher.urllib.request, "urlopen", raise_urlerror):
        assert url_fetcher._flaresolverr_fetch("https://a.com/x", "http://fs:8191") is None


def test_fetch_and_clean_passes_flaresolverr_url_from_config():
    with patch.object(url_fetcher, "fetch_page", return_value=None) as fp:
        url_fetcher._fetch_and_clean(
            "https://a.com/x", _config(flaresolverr_url="http://fs:8191")
        )
    fp.assert_called_once_with("https://a.com/x", flaresolverr_url="http://fs:8191")


def test_config_load_reads_flaresolverr_url(tmp_path):
    from kindle_email import config as cfg

    toml = tmp_path / "config.toml"
    toml.write_text(
        """
[imap]
host = "imap.example.com"
username = "u@example.com"
password = "x"

[smtp]
host = "smtp.example.com"
username = "u@example.com"
password = "x"

[kindle]
address = "k@kindle.com"
from_address = "u@example.com"

[processing]
flaresolverr_url = "http://host.docker.internal:8191"
"""
    )
    loaded = cfg.load(toml)
    assert loaded.processing.flaresolverr_url == "http://host.docker.internal:8191"
