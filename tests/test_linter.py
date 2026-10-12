"""Tests for deliverability and spam linter."""

from app.services.linter import lint_email


def make_clean_body(words_count: int = 80, link: str | None = None) -> str:
    """Helper generating a clean text body with specified word count and optional link."""
    base = (
        "Hi Alex, great catching up recently. "
        "Our team specializes in building resilient automation workflows for tech organizations. "
        "We help streamline recurring processes so engineering can focus on shipping products. "
        "Recently our clients saw huge operational gains without expanding their headcounts. "
        "Would you be open to a brief fifteen minute phone call next Tuesday? "
        "Thanks again for your consideration and looking forward to your reply. "
    )
    words = base.split()
    while len(words) < words_count:
        words.extend(["furthermore", "we", "ensure", "thorough", "testing", "standards"])
    words = words[:words_count]
    if link:
        words.append(link)
    return " ".join(words)


def test_clean_email_passes_linter() -> None:
    """Verify that a compliant plain-text cold email passes with zero failures."""
    subject = "Quick question regarding automation"
    body = make_clean_body(85, "https://agenticsoftware.com")
    result = lint_email(subject=subject, body=body, step=1)
    assert result.passed is True
    assert result.failures == []


def test_spam_trigger_words_detected() -> None:
    """Verify that forbidden spam words trigger a lint failure."""
    subject = "Guaranteed risk-free results for your business"
    body = make_clean_body(80)
    result = lint_email(subject=subject, body=body, step=1)
    assert result.passed is False
    assert any("spam trigger" in f.lower() for f in result.failures)


def test_subject_all_caps_fails() -> None:
    """Verify that an ALL CAPS subject line fails."""
    subject = "URGENT WORKFLOW PROPOSAL FOR YOU"
    body = make_clean_body(80)
    result = lint_email(subject=subject, body=body, step=1)
    assert result.passed is False
    assert any("all caps" in f.lower() for f in result.failures)


def test_subject_multiple_exclamation_marks_fails() -> None:
    """Verify that multiple exclamation points in subject line fail."""
    subject = "Exciting news for your engineering team!!"
    body = make_clean_body(80)
    result = lint_email(subject=subject, body=body, step=1)
    assert result.passed is False
    assert any("exclamation" in f.lower() for f in result.failures)


def test_more_than_one_link_in_step_1_fails() -> None:
    """Verify that step 1 emails with multiple web links fail."""
    subject = "Question about your tech stack"
    body = (
        make_clean_body(80) + " Check out https://firstlink.com and also see https://secondlink.com"
    )
    result = lint_email(subject=subject, body=body, step=1)
    assert result.passed is False
    assert any("more than 1 link" in f.lower() for f in result.failures)


def test_url_shortener_fails() -> None:
    """Verify that URL shortener domains are rejected."""
    subject = "Quick note on automation"
    body = make_clean_body(80) + " Review case studies at https://bit.ly/3xyz"
    result = lint_email(subject=subject, body=body, step=1)
    assert result.passed is False
    assert any("shortener" in f.lower() for f in result.failures)


def test_step_1_word_count_limits() -> None:
    """Verify that step 1 bodies outside 60-140 words fail."""
    subject = "Quick question"

    # Too short (< 60 words)
    short_body = "Hi there, wanted to ask if you have 15 minutes to talk about automation."
    short_res = lint_email(subject=subject, body=short_body, step=1)
    assert short_res.passed is False
    assert any("outside 60-140" in f for f in short_res.failures)

    # Too long (> 140 words)
    long_body = make_clean_body(150)
    long_res = lint_email(subject=subject, body=long_body, step=1)
    assert long_res.passed is False
    assert any("outside 60-140" in f for f in long_res.failures)


def test_html_detected_fails() -> None:
    """Verify that HTML markup is prohibited."""
    subject = "Connecting regarding engineering"
    body = make_clean_body(80) + " <p>Extra text inside paragraph tag</p>"
    result = lint_email(subject=subject, body=body, step=1)
    assert result.passed is False
    assert any("html" in f.lower() for f in result.failures)


def test_excessive_uppercase_fails() -> None:
    """Verify that emails with > 10% uppercase characters fail."""
    subject = "Checking In With Your Engineering LEADERS"
    body = "HI ALEX, WE ARE AN ENGINEERING STUDIO BUILDING BESPOKE WORKFLOWS. " + make_clean_body(
        70
    )
    result = lint_email(subject=subject, body=body, step=1)
    assert result.passed is False
    assert any("uppercase" in f.lower() for f in result.failures)


def test_unresolved_template_tags_fails() -> None:
    """Verify that leftover un-rendered '{{' or '}}' tags fail."""
    subject = "Question for {{company}}"
    body = make_clean_body(80)
    result = lint_email(subject=subject, body=body, step=1)
    assert result.passed is False
    assert any("unresolved template" in f.lower() for f in result.failures)


def test_subject_length_warning() -> None:
    """Verify that subject lines over 60 characters produce a non-blocking warning."""
    long_subject = (
        "Quick question regarding your engineering automation and backend pipelines today"
    )
    assert len(long_subject) > 60
    body = make_clean_body(80)
    result = lint_email(subject=long_subject, body=body, step=1)
    assert result.passed is True
    assert result.has_warnings is True
    assert any("exceeds 60 characters" in w for w in result.warnings)
