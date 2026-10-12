"""Lead personalization engine with strict evidence verification.

Generates ONE concise opening observation (max 25 words) grounded exclusively in
scraped text from the lead's website:
- Code-level verification:
  1. Evidence quote must literally appear in scraped text.
  2. Note must not contain hallucinated numbers, awards, or funding not present in text.
  3. Strict ban on generic flattery (impressed, love what you do, cutting-edge) and exaggeration.
  4. Never mention scraping or monitoring the site.
  5. Maximum 25 words.
- If CSV already has a personal_note, keeps it and skips the LLM.
- If scraped content is insufficient or LLM returns NONE, marks lead needs_manual_note.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.services.fetcher import WebFetcher, fetch_website
from app.services.llm import (
    LlmClient,
    LlmDailyCapExceededError,
    check_and_increment_daily_cap,
)

logger = get_logger(__name__)

# Banned generic flattery and hyperbolic marketing fluff
BANNED_FLATTERY: tuple[str, ...] = (
    "impressed",
    "love what you do",
    "love your work",
    "great company",
    "amazing work",
    "amazing company",
    "cutting-edge",
    "cutting edge",
    "world-class",
    "world class",
    "industry-leading",
    "industry leading",
    "best-in-class",
    "best in class",
    "phenomenal",
    "incredible",
    "revolutionary",
    "fascinating",
    "blown away",
    "super excited",
)

# Banned phrases referencing scraping or website monitoring
BANNED_SCRAPING: tuple[str, ...] = (
    "scraped",
    "scraping",
    "crawled",
    "crawling",
    "looking at your website",
    "checked your website",
    "checked out your website",
    "visited your website",
    "visited your site",
    "noticed on your site",
    "noticed on your website",
    "saw on your homepage",
    "saw on your website",
    "monitored your",
    "browsed your site",
    "browsing your website",
    "according to your site",
    "per your website",
)

# Number recognition regex (digits, currencies, percentages, and spelled numbers)
NUMBER_PATTERN = re.compile(
    r"\b(?:\$?\d+(?:[.,]\d+)?%?|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)\b)\b",
    re.IGNORECASE,
)

UNSUBSTANTIATED_CLAIMS = (
    "award",
    "awarded",
    "funded",
    "funding",
    "series a",
    "series b",
    "raised $",
)


def verify_personal_note(
    note: str,
    evidence_quote: str,
    scraped_text: str,
) -> tuple[bool, str | None]:
    """Verify that a proposed personal note strictly adheres to truth and tone rules.

    Args:
        note: Generated single opening sentence.
        evidence_quote: Exact supporting quote.
        scraped_text: Full text scraped from lead website.

    Returns:
        Tuple of (is_valid, rejection_reason).
    """
    clean_note = (note or "").strip()
    clean_quote = (evidence_quote or "").strip()
    clean_text = (scraped_text or "").strip().lower()

    if not clean_note:
        return False, "personal note is empty"
    if not clean_quote:
        return False, "evidence quote is empty"

    # 1. Evidence quote must literally appear in the scraped text
    if len(clean_quote) < 5 or clean_quote.lower() not in clean_text:
        return False, f"evidence quote '{clean_quote}' not found in scraped text"

    # 2. Maximum 25 words rule
    words = clean_note.split()
    if len(words) > 25:
        return False, f"note exceeds 25 words ({len(words)} words)"
    if len(words) < 4:
        return False, f"note is too short ({len(words)} words)"

    # 3. No hallucinated numbers
    numbers_in_note = NUMBER_PATTERN.findall(clean_note)
    for num in numbers_in_note:
        clean_num = num.strip().lower()
        if clean_num not in clean_text:
            return False, f"hallucinated number '{num}' not found in scraped text"

    # 4. Ban generic flattery and exaggeration
    lower_note = clean_note.lower()
    for flattery in BANNED_FLATTERY:
        if flattery in lower_note:
            return False, f"contains generic flattery: '{flattery}'"

    # 5. Ban mentions of scraping or monitoring
    for scraping_kw in BANNED_SCRAPING:
        if scraping_kw in lower_note:
            return False, f"mentions scraping/monitoring: '{scraping_kw}'"

    # 6. Unsubstantiated claims (awards, funding, capital)
    for claim in UNSUBSTANTIATED_CLAIMS:
        if claim in lower_note and claim not in clean_text:
            return False, f"unsubstantiated claim '{claim}' not found in scraped text"

    return True, None


def build_personalization_prompt(
    company: str | None,
    website: str | None,
    title: str | None,
    meta_description: str | None,
    headings: list[str],
    scraped_text: str,
) -> str:
    """Build grounded LLM prompt for single-sentence personal opening line."""
    headings_str = " | ".join(headings[:6]) if headings else "N/A"
    return f"""You are an elite B2B research assistant preparing outreach.
Your goal is to write ONE calm, human opening sentence (max 25 words) for a cold email.

Company: {company or "Target Company"}
Website: {website or "N/A"}
Page Title: {title or "N/A"}
Meta Description: {meta_description or "N/A"}
Headings: {headings_str}

Scraped Website Content:
\"\"\"
{scraped_text}
\"\"\"

HARD RULES:
1. Reference ONE specific, verifiable fact from the text (a product, service, recent post,
   or customer segment they serve).
2. Use ONLY facts present in the text above. Never invent numbers, statistics, clients, or awards.
3. If there is not enough specific info to write an observation, output exactly: NONE
4. Length: EXACTLY ONE SENTENCE, MAXIMUM 25 WORDS.
5. Tone: Calm, natural human observation.
6. FORBIDDEN:
   - NO generic flattery ("I was impressed", "love what you do", "great company", "cutting-edge").
   - NEVER mention scraping or monitoring ("I noticed on your homepage", "looked at your site").
7. Output format: Return STRICTLY JSON with these two keys:
{{
  "note": "<one sentence observation>",
  "evidence_quote": "<short exact quote from the text supporting this note>"
}}
"""


def _parse_llm_json(raw_text: str) -> dict[str, str] | None:
    """Safely extract JSON object from LLM completion string."""
    cleaned = raw_text.strip()
    if cleaned.upper() == "NONE" or cleaned.startswith("NONE"):
        return None

    # Strip markdown code blocks ```json ... ```
    if "```" in cleaned:
        code_block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if code_block:
            cleaned = code_block.group(1).strip()

    # Search for first { and last }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and start < end:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def personalize_lead(
    lead: Lead,
    db: Session,
    fetcher: WebFetcher,
    llm: LlmClient,
    clock: Clock | None = None,
) -> tuple[bool, str]:
    """Execute personalization pipeline for a single lead.

    Args:
        lead: Target Lead record.
        db: SQLAlchemy session.
        fetcher: WebFetcher implementation.
        llm: LlmClient implementation.
        clock: Optional Clock implementation.

    Returns:
        Tuple of (success_bool, message_summary).
    """
    settings = get_settings()
    clk = clock or system_clock
    now_dt = clk.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    # 1. Existing personal_note check (CSV-provided or manually edited)
    if lead.personal_note and lead.personal_note.strip():
        logger.info("Preserving existing personal note for lead #%d (%s)", lead.id, lead.email)
        if lead.status in (LeadStatus.NEW.value, LeadStatus.VALID.value):
            lead.status = (
                LeadStatus.APPROVED.value
                if settings.AUTO_APPROVE
                else LeadStatus.PERSONALIZED.value
            )
            db.commit()
        return True, "Existing personal note preserved"

    # 2. Website presence verification
    if not lead.website or not lead.website.strip():
        lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
        lead.validation_reason = "No website provided"
        db.commit()
        return False, "No website provided"

    # 3. Scrape website content
    scraped = fetch_website(lead.website, fetcher=fetcher, db=db, clock=clk)
    if not scraped.is_usable or len(scraped.visible_text) < 50:
        lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
        reason = scraped.error or "Website unreachable or thin content"
        lead.validation_reason = f"Scraping failed: {reason}"
        db.commit()
        return False, f"Scraping failed: {reason}"

    # 4. LLM daily call cap check
    try:
        check_and_increment_daily_cap(db, clock=clk)
    except LlmDailyCapExceededError as exc:
        lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
        lead.validation_reason = str(exc)
        db.commit()
        return False, str(exc)

    # 5. Generate completion from LLM
    prompt = build_personalization_prompt(
        company=lead.company,
        website=lead.website,
        title=scraped.title,
        meta_description=scraped.meta_description,
        headings=scraped.headings,
        scraped_text=scraped.visible_text,
    )

    try:
        raw_output = llm.complete(prompt)
    except Exception as exc:
        lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
        lead.validation_reason = f"LLM error: {exc}"
        db.commit()
        return False, f"LLM error: {exc}"

    # 6. Parse and verify output
    parsed_json = _parse_llm_json(raw_output)
    if not parsed_json:
        lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
        lead.validation_reason = "LLM returned NONE or invalid JSON"
        db.commit()
        return False, "LLM returned NONE or invalid format"

    note = str(parsed_json.get("note") or "").strip()
    evidence = str(parsed_json.get("evidence_quote") or "").strip()

    is_valid, rejection_reason = verify_personal_note(
        note=note,
        evidence_quote=evidence,
        scraped_text=scraped.visible_text,
    )

    if not is_valid:
        lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
        lead.validation_reason = f"Verification failed: {rejection_reason}"
        db.add(
            Event(
                lead_id=lead.id,
                type=EventType.ERROR.value,
                payload={
                    "stage": "personalization",
                    "reason": rejection_reason,
                    "rejected_note": note,
                    "rejected_evidence": evidence,
                },
                created_at=now_dt,
            )
        )
        db.commit()
        return False, f"Verification failed: {rejection_reason}"

    # 7. Success: update lead record
    lead.personal_note = note
    lead.evidence_quote = evidence
    lead.status = (
        LeadStatus.APPROVED.value if settings.AUTO_APPROVE else LeadStatus.PERSONALIZED.value
    )
    lead.validation_reason = None

    db.add(
        Event(
            lead_id=lead.id,
            type=EventType.VALIDATED.value,
            payload={
                "stage": "personalization",
                "note": note,
                "evidence_quote": evidence,
                "auto_approved": settings.AUTO_APPROVE,
            },
            created_at=now_dt,
        )
    )
    db.commit()
    logger.info("Lead #%d (%s) personalized: %s", lead.id, lead.email, note)
    return True, "Personalization generated successfully"


def personalize_campaign(
    campaign_name_or_id: str | int,
    db: Session,
    fetcher: WebFetcher,
    llm: LlmClient,
    clock: Clock | None = None,
) -> dict[str, int]:
    """Personalize all eligible leads in a campaign.

    Args:
        campaign_name_or_id: Target campaign name or ID.
        db: SQLAlchemy session.
        fetcher: WebFetcher implementation.
        llm: LlmClient implementation.
        clock: Optional Clock implementation.

    Returns:
        Summary counts dictionary.
    """
    if isinstance(campaign_name_or_id, int) or (
        isinstance(campaign_name_or_id, str) and campaign_name_or_id.isdigit()
    ):
        campaign = db.get(Campaign, int(campaign_name_or_id))
    else:
        campaign = db.scalar(select(Campaign).where(Campaign.name == str(campaign_name_or_id)))

    if not campaign:
        raise ValueError(f"Campaign '{campaign_name_or_id}' not found")

    leads = db.scalars(
        select(Lead).where(
            Lead.campaign_id == campaign.id,
            Lead.status.in_((LeadStatus.VALID.value, LeadStatus.NEW.value)),
        )
    ).all()

    stats = {
        "total": len(leads),
        "personalized": 0,
        "needs_manual_note": 0,
        "preserved": 0,
    }

    for lead in leads:
        success, msg = personalize_lead(lead, db=db, fetcher=fetcher, llm=llm, clock=clock)
        if success:
            if "preserved" in msg.lower():
                stats["preserved"] += 1
            else:
                stats["personalized"] += 1
        else:
            stats["needs_manual_note"] += 1

    return stats

@dataclass
class PersonalizeReport:
    """Report summarizing bulk personalization outcome."""

    total: int
    personalized_count: int
    preserved_count: int
    needs_manual_count: int


def personalize_leads(
    db: Session,
    campaign_id: int | str,
    fetcher: WebFetcher,
    llm_client: LlmClient,
    clock: Clock | None = None,
) -> PersonalizeReport:
    """Convenience helper to personalize eligible leads for a campaign and return a report."""
    stats = personalize_campaign(
        campaign_name_or_id=campaign_id,
        db=db,
        fetcher=fetcher,
        llm=llm_client,
        clock=clock,
    )
    return PersonalizeReport(
        total=stats["total"],
        personalized_count=stats["personalized"] + stats["preserved"],
        preserved_count=stats["preserved"],
        needs_manual_count=stats["needs_manual_note"],
    )

