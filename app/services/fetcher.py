"""Web fetcher, scraping, and HTML extraction service.

Implements SAFE BY DEFAULT web retrieval:
- WebFetcher abstract interface.
- RealFetcher: httpx client with 10s timeout, honest User-Agent, max 3 redirects,
  robots.txt compliance, max 1 request/sec per domain, max 500 KB limit, text/html only.
- FakeFetcher: in-memory mock for deterministic unit testing.
- Extraction: page title, meta description, h1/h2 headings, and visible text trimmed to 2,500 chars.
- 30-day database caching of scraped websites via WebPageCache.
"""

import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC
from html.parser import HTMLParser
from typing import Any
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.web_cache import WebPageCache

logger = get_logger(__name__)


@dataclass
class FetchResult:
    """Structured response from a single web page fetch attempt."""

    url: str
    status_code: int
    html: str
    is_success: bool
    title: str | None = None
    meta_description: str | None = None
    headings: list[str] = field(default_factory=list)
    visible_text: str = ""
    error: str | None = None


@dataclass
class ScrapedWebsite:
    """Consolidated website scraping output across homepage and secondary about page."""

    url: str
    domain: str
    title: str | None = None
    meta_description: str | None = None
    headings: list[str] = field(default_factory=list)
    visible_text: str = ""
    is_usable: bool = True
    error: str | None = None
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert scraped data to dictionary."""
        return {
            "url": self.url,
            "domain": self.domain,
            "title": self.title,
            "meta_description": self.meta_description,
            "headings": self.headings,
            "visible_text": self.visible_text,
            "is_usable": self.is_usable,
            "error": self.error,
            "from_cache": self.from_cache,
        }


class HtmlContentExtractor(HTMLParser):
    """HTML parser extracting title, meta description, h1/h2 headings, and body visible text."""

    def __init__(self, max_text_chars: int = 2500) -> None:
        super().__init__()
        self.max_text_chars = max_text_chars
        self.title: str | None = None
        self.meta_description: str | None = None
        self.headings: list[str] = []
        self._in_title = False
        self._in_heading = False
        self._current_heading: list[str] = []
        self._ignore_tags: set[str] = {
            "script",
            "style",
            "noscript",
            "svg",
            "iframe",
            "header",
            "footer",
            "nav",
        }
        self._tag_stack: list[str] = []
        self._text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        self._tag_stack.append(tag_lower)

        if tag_lower == "title":
            self._in_title = True
        elif tag_lower in ("h1", "h2"):
            self._in_heading = True
            self._current_heading = []
        elif tag_lower == "meta":
            attr_dict = {k.lower(): (v or "") for k, v in attrs}
            name = attr_dict.get("name", "").lower()
            prop = attr_dict.get("property", "").lower()
            if name == "description" or prop in ("description", "og:description"):
                content = attr_dict.get("content", "").strip()
                if content and not self.meta_description:
                    self.meta_description = content

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "title":
            self._in_title = False
        elif tag_lower in ("h1", "h2"):
            self._in_heading = False
            heading_text = " ".join("".join(self._current_heading).split())
            if heading_text and heading_text not in self.headings:
                self.headings.append(heading_text)

        if self._tag_stack and self._tag_stack[-1] == tag_lower:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        # Ignore text inside non-visible or script/style tags
        if any(ignored in self._tag_stack for ignored in self._ignore_tags):
            return

        text = data.strip()
        if not text:
            return

        if self._in_title and not self.title:
            self.title = text
        if self._in_heading:
            self._current_heading.append(data)

        self._text_chunks.append(data)

    def extract(self) -> tuple[str | None, str | None, list[str], str]:
        """Return parsed (title, meta_description, headings, visible_text)."""
        raw_text = " ".join(" ".join(self._text_chunks).split())
        trimmed_text = raw_text[: self.max_text_chars].strip()
        return (
            self.title,
            self.meta_description,
            self.headings,
            trimmed_text,
        )


def extract_html_metadata(
    html: str, max_text_chars: int = 2500
) -> tuple[str | None, str | None, list[str], str]:
    """Parse raw HTML and extract title, meta description, headings, and visible text."""
    if not html or not html.strip():
        return None, None, [], ""
    extractor = HtmlContentExtractor(max_text_chars=max_text_chars)
    try:
        extractor.feed(html)
        extractor.close()
        return extractor.extract()
    except Exception as err:
        logger.debug("HTML extraction error: %s", err)
        return None, None, [], ""


class WebFetcher(ABC):
    """Abstract interface for fetching web page content."""

    @abstractmethod
    def get(self, url: str) -> FetchResult:
        """Fetch web page at URL and return parsed FetchResult."""
        pass


class RealFetcher(WebFetcher):
    """Production HTTP web fetcher respecting robots.txt, rate limits, and size bounds."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.user_agent = f"OutreachBot (contact: {self.settings.SMTP_FROM_EMAIL})"
        self.headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        self.timeout = self.settings.WEB_FETCHER_TIMEOUT_SECONDS
        self.max_bytes = self.settings.WEB_FETCHER_MAX_BYTES
        self._robots_parsers: dict[str, RobotFileParser] = {}
        self._last_request_time: dict[str, float] = {}

    def _get_domain(self, url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        return parsed.netloc.lower()

    def _can_fetch(self, url: str) -> bool:
        domain = self._get_domain(url)
        if not domain:
            return True

        if domain not in self._robots_parsers:
            rp = RobotFileParser()
            parsed = urllib.parse.urlsplit(url)
            scheme = parsed.scheme or "https"
            robots_url = f"{scheme}://{domain}/robots.txt"
            try:
                resp = httpx.get(
                    robots_url,
                    headers=self.headers,
                    timeout=5.0,
                    follow_redirects=True,
                )
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp.allow_all = True
            except Exception:
                rp.allow_all = True
            self._robots_parsers[domain] = rp

        rp = self._robots_parsers[domain]
        try:
            return bool(rp.can_fetch(self.user_agent, url))
        except Exception:
            return True

    def _throttle(self, domain: str) -> None:
        """Enforce max 1 request per second per domain rate limit."""
        if domain in self._last_request_time:
            elapsed = time.time() - self._last_request_time[domain]
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
        self._last_request_time[domain] = time.time()

    def get(self, url: str) -> FetchResult:
        """Fetch page with rate limiting, size limits, and robots.txt check."""
        domain = self._get_domain(url)

        # 1. Robots.txt check
        if not self._can_fetch(url):
            logger.info("URL %s is disallowed by robots.txt", url)
            return FetchResult(
                url=url,
                status_code=403,
                html="",
                is_success=False,
                error="Blocked by robots.txt",
            )

        # 2. Domain throttling (max 1 req/sec)
        self._throttle(domain)

        # 3. HTTP Request
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                max_redirects=self.settings.WEB_FETCHER_MAX_REDIRECTS,
                headers=self.headers,
            ) as client:
                response = client.get(url)

                # Content-Type check: only text/html
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    return FetchResult(
                        url=url,
                        status_code=response.status_code,
                        html="",
                        is_success=False,
                        error=f"Unsupported Content-Type: {content_type}",
                    )

                # Max 500 KB size check
                raw_bytes = response.content[: self.max_bytes]
                try:
                    html_text = raw_bytes.decode(response.encoding or "utf-8", errors="replace")
                except Exception:
                    html_text = raw_bytes.decode("utf-8", errors="replace")

                if response.status_code >= 400:
                    return FetchResult(
                        url=url,
                        status_code=response.status_code,
                        html=html_text,
                        is_success=False,
                        error=f"HTTP {response.status_code}",
                    )

                title, meta_desc, headings, visible_text = extract_html_metadata(html_text)
                return FetchResult(
                    url=url,
                    status_code=response.status_code,
                    html=html_text,
                    is_success=True,
                    title=title,
                    meta_description=meta_desc,
                    headings=headings,
                    visible_text=visible_text,
                )
        except httpx.TimeoutException:
            return FetchResult(
                url=url,
                status_code=408,
                html="",
                is_success=False,
                error="Request timed out (10s)",
            )
        except Exception as exc:
            return FetchResult(
                url=url,
                status_code=0,
                html="",
                is_success=False,
                error=f"Fetch error: {exc}",
            )


class FakeFetcher(WebFetcher):
    """In-memory web fetcher for deterministic testing."""

    def __init__(self, pages: dict[str, str | FetchResult] | None = None) -> None:
        self.pages: dict[str, FetchResult] = {}
        self.disallowed_domains: set[str] = set()
        self.fetched_urls: list[str] = []

        if pages:
            for url, val in pages.items():
                norm = normalize_url(url)
                if isinstance(val, FetchResult):
                    self.pages[norm] = val
                else:
                    title, meta_desc, headings, visible = extract_html_metadata(val)
                    self.pages[norm] = FetchResult(
                        url=norm,
                        status_code=200,
                        html=val,
                        is_success=True,
                        title=title,
                        meta_description=meta_desc,
                        headings=headings,
                        visible_text=visible,
                    )

    def set_page(
        self, url: str, html: str, status_code: int = 200, error: str | None = None
    ) -> None:
        """Register a canned HTML page for a URL."""
        norm = normalize_url(url)
        is_success = status_code < 400 and error is None
        title, meta_desc, headings, visible = extract_html_metadata(html)
        self.pages[norm] = FetchResult(
            url=norm,
            status_code=status_code,
            html=html,
            is_success=is_success,
            title=title,
            meta_description=meta_desc,
            headings=headings,
            visible_text=visible,
            error=error,
        )

    def disallow_domain(self, domain: str) -> None:
        """Mark a domain as disallowed by robots.txt."""
        self.disallowed_domains.add(domain.lower())

    def get(self, url: str) -> FetchResult:
        """Retrieve canned result or return 404."""
        norm = normalize_url(url)
        self.fetched_urls.append(norm)

        domain = urllib.parse.urlsplit(norm).netloc.lower()
        if domain in self.disallowed_domains:
            return FetchResult(
                url=norm,
                status_code=403,
                html="",
                is_success=False,
                error="Blocked by robots.txt",
            )

        if norm in self.pages:
            return self.pages[norm]

        # Check domain-only root fallback
        root_url = f"{urllib.parse.urlsplit(norm).scheme}://{domain}"
        if root_url in self.pages and norm.rstrip("/") == root_url:
            return self.pages[root_url]

        return FetchResult(
            url=norm,
            status_code=404,
            html="",
            is_success=False,
            error="404 Not Found",
        )


def normalize_url(url: str) -> str:
    """Normalize input URL with proper scheme and trailing formatting."""
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    parsed = urllib.parse.urlsplit(cleaned)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    return urllib.parse.urlunsplit((scheme, netloc, path, parsed.query, ""))


def fetch_website(
    url: str,
    fetcher: WebFetcher,
    db: Session,
    clock: Clock | None = None,
) -> ScrapedWebsite:
    """Fetch website homepage and secondary about page, caching for 30 days.

    Args:
        url: Lead website URL.
        fetcher: WebFetcher implementation.
        db: SQLAlchemy session.
        clock: Optional Clock implementation.

    Returns:
        ScrapedWebsite with title, description, headings, and visible text.
    """
    normalized_base = normalize_url(url)
    if not normalized_base:
        return ScrapedWebsite(
            url="",
            domain="",
            is_usable=False,
            error="Missing website URL",
        )

    domain = urllib.parse.urlsplit(normalized_base).netloc.lower()
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    # 1. Check 30-day database cache
    cached_record = db.scalar(select(WebPageCache).where(WebPageCache.url == normalized_base))
    if cached_record and cached_record.is_fresh(now, ttl_days=30):
        if not cached_record.is_success or len(cached_record.visible_text) < 50:
            return ScrapedWebsite(
                url=normalized_base,
                domain=domain,
                title=cached_record.title,
                meta_description=cached_record.meta_description,
                headings=cached_record.headings or [],
                visible_text=cached_record.visible_text,
                is_usable=False,
                error=cached_record.error or "Cached site has insufficient content",
                from_cache=True,
            )

        return ScrapedWebsite(
            url=normalized_base,
            domain=domain,
            title=cached_record.title,
            meta_description=cached_record.meta_description,
            headings=cached_record.headings or [],
            visible_text=cached_record.visible_text,
            is_usable=True,
            from_cache=True,
        )

    # 2. Fetch homepage
    home_res = fetcher.get(normalized_base)
    if not home_res.is_success:
        err = home_res.error or f"HTTP {home_res.status_code}"
        _save_cache(
            db,
            url=normalized_base,
            domain=domain,
            res=home_res,
            is_success=False,
            error=err,
            clock=clk,
        )
        return ScrapedWebsite(
            url=normalized_base,
            domain=domain,
            is_usable=False,
            error=err,
        )

    combined_headings = list(home_res.headings)
    text_parts = [home_res.visible_text] if home_res.visible_text else []

    # 3. Try to fetch secondary about page (/about, /about-us, /company)
    parsed = urllib.parse.urlsplit(normalized_base)
    scheme_netloc = f"{parsed.scheme}://{domain}"

    about_candidates = [
        f"{scheme_netloc}/about",
        f"{scheme_netloc}/about-us",
        f"{scheme_netloc}/company",
    ]

    for candidate_url in about_candidates:
        if candidate_url == normalized_base:
            continue
        about_res = fetcher.get(candidate_url)
        if about_res.is_success and len(about_res.visible_text) >= 50:
            for h in about_res.headings:
                if h not in combined_headings:
                    combined_headings.append(h)
            if about_res.visible_text:
                text_parts.append(about_res.visible_text)
            break  # Pick first available secondary about page

    full_visible_text = " ".join(" ".join(text_parts).split())[:2500].strip()

    # Verify site content sufficiency
    is_usable = len(full_visible_text) >= 50 and not _is_js_placeholder(full_visible_text)
    error_msg = None if is_usable else "Insufficient content or JavaScript placeholder"

    final_res = FetchResult(
        url=normalized_base,
        status_code=home_res.status_code,
        html=home_res.html,
        is_success=is_usable,
        title=home_res.title,
        meta_description=home_res.meta_description,
        headings=combined_headings,
        visible_text=full_visible_text,
        error=error_msg,
    )

    _save_cache(
        db,
        url=normalized_base,
        domain=domain,
        res=final_res,
        is_success=is_usable,
        error=error_msg,
        clock=clk,
    )

    return ScrapedWebsite(
        url=normalized_base,
        domain=domain,
        title=home_res.title,
        meta_description=home_res.meta_description,
        headings=combined_headings,
        visible_text=full_visible_text,
        is_usable=is_usable,
        error=error_msg,
        from_cache=False,
    )


def _is_js_placeholder(text: str) -> bool:
    """Detect if visible text is just an empty JavaScript requirement notice."""
    lower = text.lower()
    return (
        "enable javascript" in lower
        or "javascript is required" in lower
        or "need to enable javascript" in lower
    ) and len(text) < 150


def _save_cache(
    db: Session,
    url: str,
    domain: str,
    res: FetchResult,
    is_success: bool,
    error: str | None,
    clock: Clock,
) -> None:
    """Save or update WebPageCache entry."""
    now = clock.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    cache = db.scalar(select(WebPageCache).where(WebPageCache.url == url))
    if cache:
        cache.title = res.title
        cache.meta_description = res.meta_description
        cache.headings = res.headings
        cache.visible_text = res.visible_text
        cache.status_code = res.status_code
        cache.is_success = is_success
        cache.error = error
        cache.updated_at = now
    else:
        cache = WebPageCache(
            url=url,
            domain=domain,
            title=res.title,
            meta_description=res.meta_description,
            headings=res.headings,
            visible_text=res.visible_text,
            status_code=res.status_code,
            is_success=is_success,
            error=error,
            created_at=now,
            updated_at=now,
        )
        db.add(cache)
    db.commit()
