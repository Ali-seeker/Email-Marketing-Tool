"""Spam and deliverability linter for cold outbound emails.

Evaluates rendered subject lines and email bodies against strict spam triggers,
structural rules, link thresholds, and formatting standards.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

# URL shortener domains prohibited from cold outreach
URL_SHORTENERS = frozenset(
    [
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "ow.ly",
        "buff.ly",
        "is.gd",
        "cutt.ly",
        "rebrand.ly",
        "bl.ink",
        "shorturl.at",
    ]
)

# Common HTML tag pattern
HTML_TAG_REGEX = re.compile(r"<[a-zA-Z/][^>]*>", re.IGNORECASE)

# URL detection pattern
URL_REGEX = re.compile(r"https?://[^\s]+|www\.[^\s]+", re.IGNORECASE)


def _load_spam_triggers() -> list[str]:
    """Load spam trigger words and phrases from data file."""
    data_file = Path(__file__).resolve().parent.parent / "data" / "spam_triggers.txt"
    triggers: list[str] = []
    if data_file.exists():
        with open(data_file, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip().lower()
                if stripped and not stripped.startswith("#"):
                    triggers.append(stripped)
    return sorted(triggers, key=len, reverse=True)


SPAM_TRIGGERS = _load_spam_triggers()


@dataclass
class LintResult:
    """Outcome of spam and deliverability linting."""

    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_warnings(self) -> bool:
        """Check if linter produced non-blocking warnings."""
        return len(self.warnings) > 0


def _find_spam_triggers(text: str) -> list[str]:
    """Search text for forbidden spam trigger words or phrases."""
    found: list[str] = []
    lower_text = text.lower()
    for trigger in SPAM_TRIGGERS:
        # Match trigger at word boundaries or symbol bounds
        pattern = rf"(?:\b|(?<=\W)){re.escape(trigger)}(?:\b|(?=\W))"
        if re.search(pattern, lower_text):
            found.append(trigger)
    return found


def _extract_urls(text: str) -> list[str]:
    """Extract all web links from text."""
    return URL_REGEX.findall(text)


def lint_email(subject: str, body: str, step: int = 1) -> LintResult:
    """Evaluate subject and body against all spam and quality rules.

    Args:
        subject: Rendered email subject line.
        body: Rendered plain-text email body.
        step: Sequence step index (1, 2, 3, 4).

    Returns:
        LintResult with passed flag, failures list, and warnings list.
    """
    failures: list[str] = []
    warnings: list[str] = []

    combined_text = f"{subject}\n{body}"

    # 1. Spam trigger words/phrases check
    triggers_found = _find_spam_triggers(combined_text)
    if triggers_found:
        unique_triggers = sorted(set(triggers_found))
        failures.append(f"Spam trigger words/phrases detected: {', '.join(unique_triggers)}")

    # 2. Subject ALL CAPS check
    stripped_subject = subject.strip()
    alpha_subject = [c for c in stripped_subject if c.isalpha()]
    if alpha_subject and all(c.isupper() for c in alpha_subject):
        failures.append("Subject is ALL CAPS")

    # 3. Subject exclamation marks check
    if subject.count("!") > 1:
        failures.append("Subject contains multiple exclamation marks ('!')")

    # 4. Links in step 1 check
    urls = _extract_urls(body)
    if step == 1 and len(urls) > 1:
        failures.append(f"Step 1 contains more than 1 link ({len(urls)} links detected)")

    # 5. URL shorteners check
    for url in urls:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        host = (parsed.netloc or "").lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        if host in URL_SHORTENERS:
            failures.append(f"URL shortener domain detected: '{host}'")

    # 6. Step 1 body word count (60 - 140 words)
    words = body.split()
    word_count = len(words)
    if step == 1 and (word_count < 60 or word_count > 140):
        failures.append(f"Step 1 body word count ({word_count}) is outside 60-140 words range")

    # 7. Attachments or HTML detected
    if HTML_TAG_REGEX.search(body) or HTML_TAG_REGEX.search(subject):
        failures.append("HTML markup detected in email (plain text required)")

    # 8. Uppercase letter percentage (> 10%)
    total_letters = sum(1 for c in combined_text if c.isalpha())
    upper_letters = sum(1 for c in combined_text if c.isupper())
    if total_letters > 20 and (upper_letters / total_letters) > 0.10:
        pct = (upper_letters / total_letters) * 100
        failures.append(f"More than 10% uppercase letters detected across email ({pct:.1f}%)")

    # 9. Unresolved template tags left in text
    if "{{" in combined_text or "}}" in combined_text:
        failures.append(
            "Unresolved template variable tags ('{{' or '}}') detected in rendered email"
        )

    # Warning rule: Subject longer than 60 characters
    if len(subject) > 60:
        warnings.append(f"Subject line exceeds 60 characters ({len(subject)} characters)")

    passed = len(failures) == 0
    return LintResult(passed=passed, failures=failures, warnings=warnings)
