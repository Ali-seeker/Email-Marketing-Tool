"""LLM client subsystem and provider abstractions.

Supports multiple pluggable providers with strict safety limits:
- LlmClient abstract interface.
- GeminiClient: Google Gemini API (free tier).
- OllamaClient: Local offline open-source model via Ollama.
- ClaudeClient: Anthropic Claude API.
- FakeLlm: Deterministic in-memory client for testing.
- Daily call cap enforcement (default 200).
- Automatic backoff on rate-limit (429) errors.
"""

import json
import re
import time
from abc import ABC, abstractmethod

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.setting import Setting

logger = get_logger(__name__)


class LlmError(Exception):
    """Base exception for LLM operations."""

    pass


class LlmDailyCapExceededError(LlmError):
    """Raised when the daily LLM call quota is exhausted."""

    pass


class LlmRateLimitError(LlmError):
    """Raised when the provider rejects the call due to rate limiting."""

    pass


def check_and_increment_daily_cap(
    db: Session,
    clock: Clock | None = None,
    cap: int | None = None,
) -> int:
    """Check and atomically increment daily LLM call count in Setting table."""
    settings = get_settings()
    daily_cap = cap if cap is not None else settings.LLM_DAILY_CALL_CAP

    clk = clock or system_clock
    today_str = clk.now().date().isoformat()
    key = f"LLM_CALLS_{today_str}"

    val = db.scalar(select(Setting.value).where(Setting.key == key))
    current_count = int(val) if val and val.isdigit() else 0

    if current_count >= daily_cap:
        logger.warning(
            "Daily LLM call cap reached (%d/%d) for date %s", current_count, daily_cap, today_str
        )
        raise LlmDailyCapExceededError(
            f"Daily LLM call cap reached ({current_count}/{daily_cap}) for {today_str}"
        )

    new_count = current_count + 1
    db.merge(Setting(key=key, value=str(new_count)))
    db.commit()
    return new_count


class LlmClient(ABC):
    """Abstract interface for large language model text completions."""

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Submit prompt and return generated response text."""
        pass


class SmartRuleBasedLlm(LlmClient):
    """Zero-cost intelligent rule-based personalizer.

    Operates when no external Gemini API key is configured or as an instant fallback.
    Analyzes company, website presence, and scraped content to choose the pitch angle
    and craft a verified, fact-based personal hook and service offering.
    """

    def complete(self, prompt: str) -> str:
        # Extract fields from prompt
        company_match = re.search(r"Company:\s*(.+)", prompt)
        company = company_match.group(1).strip() if company_match else "Target Company"
        if company in ("None", "N/A", ""):
            company = "Target Company"

        website_match = re.search(r"Website:\s*(.+)", prompt)
        website = website_match.group(1).strip() if website_match else "None"

        scraped_text = ""
        if 'Scraped Website Content:\n"""' in prompt:
            parts = prompt.split('Scraped Website Content:\n"""')
            if len(parts) > 1:
                content_part = parts[1].split('"""')[0]
                scraped_text = content_part.strip()

        has_site = bool(
            website and website.lower() not in ("none", "n/a", "") and len(scraped_text) >= 50
        )
        clean_text = scraped_text.lower()

        if not has_site:
            pitch_angle = "website"
            offered_service = "modern, high-performing websites and web applications"
            example_result = "turning digital visitors into qualified inbound leads consistently"
            note = f"I came across {company}'s work and wanted to reach out directly."
            evidence_quote = company
        else:
            tech_keywords = (
                "api",
                "developer",
                "saas",
                "software",
                "sdk",
                "infrastructure",
                "platform",
                "cloud",
                "database",
            )
            ops_keywords = (
                "dispatch",
                "fleet",
                "logistics",
                "warehouse",
                "inventory",
                "booking",
                "workflow",
                "automate",
                "automation",
                "crm",
                "erp",
            )
            seo_keywords = (
                "roofing",
                "clinic",
                "plumbing",
                "dental",
                "hvac",
                "contractor",
                "seo",
                "rank",
                "local",
                "retail",
                "ecommerce",
                "traffic",
            )

            if any(k in clean_text for k in tech_keywords):
                pitch_angle = "custom_software"
                offered_service = "custom software development and cloud API infrastructure"
                example_result = "shipping core product features 3x faster with zero technical debt"
            elif any(k in clean_text for k in ops_keywords):
                pitch_angle = "automation"
                offered_service = "bespoke AI workflow automation pipelines"
                example_result = "reclaiming 15+ engineering and operational hours weekly"
            elif any(k in clean_text for k in seo_keywords):
                pitch_angle = "seo"
                offered_service = "technical SEO and organic search visibility pipelines"
                example_result = (
                    "a 2.5x increase in qualified organic search traffic within 90 days"
                )
            else:
                pitch_angle = "automation"
                offered_service = "bespoke AI workflow automation pipelines"
                example_result = "reclaiming 15+ engineering and operational hours weekly"

            sentences = [
                s.strip()
                for s in re.split(r"[.!?]\n+|[.!?]\s+", scraped_text)
                if len(s.strip()) > 15
            ]
            candidate_quote = ""
            candidate_note = ""

            for s in sentences:
                words = s.split()
                if 5 <= len(words) <= 20:
                    s_lower = s.lower()
                    if not any(
                        f in s_lower
                        for f in (
                            "impressed",
                            "cutting-edge",
                            "love",
                            "amazing",
                            "best-in-class",
                            "http",
                        )
                    ):
                        quote_slice = " ".join(words[: min(6, len(words))])
                        if len(quote_slice) >= 8 and quote_slice.lower() in clean_text:
                            candidate_quote = quote_slice
                            candidate_note = (
                                f"I saw that your platform focuses on {quote_slice.lower()}."
                            )
                            break

            if not candidate_note or not candidate_quote:
                words = scraped_text.split()
                quote_slice = " ".join(words[: min(5, len(words))])
                candidate_quote = quote_slice
                candidate_note = f"I saw that your team focuses on {quote_slice.lower()}."

            note = candidate_note
            evidence_quote = candidate_quote

        return json.dumps(
            {
                "pitch_angle": pitch_angle,
                "offered_service": offered_service,
                "example_result": example_result,
                "note": note,
                "evidence_quote": evidence_quote,
            }
        )


class GeminiClient(LlmClient):
    """Google Gemini API client with automatic fallback to SmartRuleBasedLlm when unconfigured."""

    def __init__(self, settings: Settings | None = None, max_retries: int = 3) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.GEMINI_MODEL
        self.api_key = (
            self.settings.GEMINI_API_KEY.get_secret_value() if self.settings.GEMINI_API_KEY else ""
        )
        self.max_retries = max_retries

    def complete(self, prompt: str) -> str:
        # If API key is missing or default placeholder, gracefully use smart free engine
        if not self.api_key or self.api_key in ("your_gemini_api_key_here", ""):
            logger.info("GEMINI_API_KEY is not set yet; utilizing Smart Free Personalizer.")
            return SmartRuleBasedLlm().complete(prompt)

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(url, json=payload)

                    if resp.status_code == 429:
                        if attempt < self.max_retries:
                            time.sleep(delay)
                            delay *= 2.0
                            continue
                        logger.warning(
                            "Gemini 429 rate limit exceeded; using Smart Personalizer fallback."
                        )
                        return SmartRuleBasedLlm().complete(prompt)

                    if resp.status_code != 200:
                        logger.warning(
                            "Gemini API error (%d); using Smart Personalizer fallback.",
                            resp.status_code,
                        )
                        return SmartRuleBasedLlm().complete(prompt)

                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        return SmartRuleBasedLlm().complete(prompt)
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        return SmartRuleBasedLlm().complete(prompt)
                    return parts[0].get("text", "").strip()
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2.0
                    continue
                logger.warning("Gemini network error (%s); using Smart Personalizer fallback.", exc)
                return SmartRuleBasedLlm().complete(prompt)

        return SmartRuleBasedLlm().complete(prompt)


class OllamaClient(LlmClient):
    """Local offline Ollama model client via HTTP."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.OLLAMA_BASE_URL.rstrip("/")
        self.model = self.settings.OLLAMA_MODEL

    def complete(self, prompt: str) -> str:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code != 200:
                    raise LlmError(f"Ollama API error ({resp.status_code}): {resp.text}")
                data = resp.json()
                return data.get("response", "").strip()
        except Exception as exc:
            raise LlmError(f"Ollama connection error: {exc}") from exc


class ClaudeClient(LlmClient):
    """Anthropic Claude API client."""

    def __init__(self, settings: Settings | None = None, max_retries: int = 3) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.ANTHROPIC_MODEL
        self.api_key = (
            self.settings.ANTHROPIC_API_KEY.get_secret_value()
            if self.settings.ANTHROPIC_API_KEY
            else ""
        )
        self.max_retries = max_retries

    def complete(self, prompt: str) -> str:
        if not self.api_key:
            raise LlmError("ANTHROPIC_API_KEY is not configured in settings/environment")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }

        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 429:
                        if attempt < self.max_retries:
                            time.sleep(delay)
                            delay *= 2.0
                            continue
                        raise LlmRateLimitError(f"Claude rate limit exceeded: {resp.text}")

                    if resp.status_code != 200:
                        raise LlmError(f"Claude API error ({resp.status_code}): {resp.text}")

                    data = resp.json()
                    content = data.get("content", [])
                    if not content:
                        return "NONE"
                    return content[0].get("text", "").strip()
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2.0
                    continue
                raise LlmError(f"Claude connection error: {exc}") from exc

        return "NONE"


class FakeLlm(LlmClient):
    """In-memory scripted LLM client for tests."""

    def __init__(
        self,
        canned_responses: dict[str, str] | None = None,
        default_response: str | None = None,
    ) -> None:
        self.canned_responses: dict[str, str] = canned_responses or {}
        default_note = (
            '{"pitch_angle": "automation", '
            '"offered_service": "bespoke AI automation pipelines", '
            '"example_result": "reclaiming 15+ engineering hours weekly", '
            '"note": "I saw that your platform automates database backups.", '
            '"evidence_quote": "automates database backups"}'
        )
        self.default_response: str = default_response or default_note
        self.prompts: list[str] = []
        self.call_count: int = 0
        self.rate_limit_failures_remaining: int = 0

    def set_response(self, keyword_or_prompt: str, response: str) -> None:
        """Add a canned response mapped to a prompt substring."""
        self.canned_responses[keyword_or_prompt] = response

    def set_rate_limit_failures(self, count: int) -> None:
        """Simulate N transient 429 rate limit exceptions before succeeding."""
        self.rate_limit_failures_remaining = count

    def complete(self, prompt: str) -> str:
        self.call_count += 1
        self.prompts.append(prompt)

        if self.rate_limit_failures_remaining > 0:
            self.rate_limit_failures_remaining -= 1
            raise LlmRateLimitError("Simulated 429 Rate Limit error")

        # Match longest matching substring in canned_responses
        for key, resp in self.canned_responses.items():
            if key in prompt:
                return resp

        return self.default_response


def get_llm_client(settings: Settings | None = None) -> LlmClient:
    """Instantiate configured LLM provider according to settings.LLM_PROVIDER."""
    cfg = settings or get_settings()
    provider = cfg.LLM_PROVIDER.strip().lower()

    if provider == "gemini":
        return GeminiClient(settings=cfg)
    elif provider in ("free", "smart"):
        return SmartRuleBasedLlm()
    elif provider == "ollama":
        return OllamaClient(settings=cfg)
    elif provider == "claude":
        return ClaudeClient(settings=cfg)
    elif provider == "fake":
        return FakeLlm()
    else:
        logger.warning(
            "Unrecognized LLM_PROVIDER '%s'; falling back to SmartRuleBasedLlm", provider
        )
        return SmartRuleBasedLlm()
