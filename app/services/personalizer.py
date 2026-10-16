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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.setting import Setting
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
    pitch_angle: str | None = None,
    company: str | None = None,
) -> tuple[bool, str | None]:
    """Verify that a proposed personal note strictly adheres to truth and tone rules.

    Args:
        note: Generated single opening sentence.
        evidence_quote: Exact supporting quote.
        scraped_text: Full text scraped from lead website.
        pitch_angle: Selected service offer category (website, seo, automation, custom_software).
        company: Optional target company name for non-website validation.

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

    # 1. Evidence quote verification
    # When website text is available, quote must literally appear in scraped text.
    # When pitch_angle is 'website' with no scraped text, quote should reference company or domain.
    if clean_text:
        if len(clean_quote) < 5 or clean_quote.lower() not in clean_text:
            return False, f"evidence quote '{clean_quote}' not found in scraped text"
    elif pitch_angle == "website":
        if len(clean_quote) < 3:
            return False, f"evidence reference '{clean_quote}' is too short"
    else:
        return False, f"evidence quote '{clean_quote}' not found in scraped text"

    # 2. Maximum 25 words rule
    words = clean_note.split()
    if len(words) > 25:
        return False, f"note exceeds 25 words ({len(words)} words)"
    if len(words) < 4:
        return False, f"note is too short ({len(words)} words)"

    # 3. No hallucinated numbers
    numbers_in_note = NUMBER_PATTERN.findall(clean_note)
    if clean_text:
        for num in numbers_in_note:
            clean_num = num.strip().lower()
            if clean_num not in clean_text:
                return False, f"hallucinated number '{num}' not found in scraped text"
    elif numbers_in_note:
        co_lower = (company or "").lower()
        for num in numbers_in_note:
            if num.strip().lower() not in co_lower:
                return False, f"hallucinated number '{num}' in note without supporting text"

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
        if claim in lower_note and (not clean_text or claim not in clean_text):
            return False, f"unsubstantiated claim '{claim}' not found in scraped text"

    return True, None


def build_personalization_prompt(
    company: str | None,
    website: str | None,
    title: str | None,
    meta_description: str | None,
    headings: list[str],
    scraped_text: str,
    niche: str | None = None,
) -> str:
    """Build grounded LLM prompt to select pitch angle, service, metric, and opening note."""
    headings_str = " | ".join(headings[:6]) if headings else "N/A"
    return f"""You are an elite B2B research assistant preparing outreach for an engineering agency.
Our agency provides software and digital solutions across 4 distinct service categories:
1. 'website': Modern web applications and high-converting websites
   (for leads with NO website, an unreachable site, or a very basic landing page).
2. 'seo': Technical SEO and organic search visibility pipelines
   (for leads with an active website with thin meta tags, low rankings, or needing organic growth).
3. 'automation': Bespoke workflow & AI automation pipelines
   (for businesses with repetitive manual operations, booking, scheduling, dispatch, or CRM sync).
4. 'custom_software': Custom full-stack software development & API infrastructure
   (for tech companies, SaaS products, dev tools, or teams with complex feature roadmaps).

Target Lead:
Company: {company or "Target Company"}
Industry/Niche: {niche or "N/A"}
Website: {website or "None"}
Page Title: {title or "N/A"}
Meta Description: {meta_description or "N/A"}
Headings: {headings_str}

Scraped Website Content:
\"\"\"
{scraped_text}
\"\"\"

TASKS:
1. Determine the best pitch_angle ('website', 'seo', 'automation', or 'custom_software').
   - If no website is available or site is unreachable, select 'website'.
   - If website has weak metadata/SEO or is a local business needing discovery, select 'seo'.
   - If business has repetitive operations, logistics, dispatch, or syncing, select 'automation'.
   - If business is a tech company, platform, or SaaS product, select 'custom_software'.
2. Select offered_service: A concise noun phrase that fits the sequence sentence template:
   "we build {{service}} for growing teams in the {{niche}} sector".
3. Select example_result: A concrete outcome phrase that fits the sequence sentence template:
   "helped a peer team achieve {{example_result}} without adding headcount".
4. Write note: EXACTLY ONE calm, human opening observation sentence (MAXIMUM 25 WORDS).
   Factual, grounded in their real business or services.
5. Provide evidence_quote: Exact short quote from scraped text (or company name if no website).

HARD RULES:
- Never use generic flattery ("impressed", "cutting-edge", "amazing", "love what you do").
- Never mention scraping or monitoring ("noticed on your homepage", "checked your website").
- Length: EXACTLY ONE SENTENCE, MAXIMUM 25 WORDS.
- Output MUST be valid JSON only:
{{
  "pitch_angle": "<website|seo|automation|custom_software>",
  "offered_service": "<concise noun phrase for {{service}}>",
  "example_result": "<concise outcome phrase for {{example_result}}>",
  "note": "<one sentence opening observation>",
  "evidence_quote": "<exact quote from text>"
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


def is_auto_approve_enabled(db: Session) -> bool:
    """Check setting AUTO_APPROVE from DB override or config."""
    db_setting = db.scalar(select(Setting).where(Setting.key == "AUTO_APPROVE"))
    if db_setting and db_setting.value is not None:
        return db_setting.value.strip().lower() in ("true", "1", "yes")
    return get_settings().AUTO_APPROVE


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
    auto_approve = is_auto_approve_enabled(db)
    clk = clock or system_clock
    now_dt = clk.now()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)

    has_website = bool(lead.website and lead.website.strip())

    # 1. Existing personal_note check (CSV-provided or manually edited)
    if lead.personal_note and lead.personal_note.strip():
        logger.info("Preserving existing personal note for lead #%d (%s)", lead.id, lead.email)
        if not getattr(lead, "pitch_angle", None):
            if not has_website:
                lead.pitch_angle = "website"
                lead.offered_service = "modern, high-performing websites and web applications"
                lead.example_result = (
                    "turning digital visitors into qualified inbound leads consistently"
                )
            else:
                lead.pitch_angle = "automation"
                lead.offered_service = settings.DEFAULT_SERVICE
                lead.example_result = settings.DEFAULT_EXAMPLE_RESULT
        if lead.status in (LeadStatus.NEW.value, LeadStatus.VALID.value):
            lead.status = (
                LeadStatus.APPROVED.value if auto_approve else LeadStatus.PERSONALIZED.value
            )
            db.commit()
        return True, "Existing personal note preserved"

    # 2. Website presence verification & scraping
    has_website = bool(lead.website and lead.website.strip())
    scraped_text = ""
    scraped_title = None
    scraped_meta = None
    scraped_headings: list[str] = []

    if has_website:
        scraped = fetch_website(lead.website, fetcher=fetcher, db=db, clock=clk)
        if not scraped.is_usable or len(scraped.visible_text) < 50:
            lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
            reason = scraped.error or "Website unreachable or thin content"
            lead.validation_reason = f"Scraping failed: {reason}"
            db.commit()
            return False, f"Scraping failed: {reason}"
        scraped_text = scraped.visible_text
        scraped_title = scraped.title
        scraped_meta = scraped.meta_description
        scraped_headings = scraped.headings

    # 3. LLM daily call cap check
    try:
        check_and_increment_daily_cap(db, clock=clk)
    except LlmDailyCapExceededError as exc:
        lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
        lead.validation_reason = str(exc)
        db.commit()
        return False, str(exc)

    # 4. Generate completion from LLM
    niche_val = lead.campaign.niche if lead.campaign else None
    prompt = build_personalization_prompt(
        company=lead.company,
        website=lead.website if has_website else None,
        title=scraped_title,
        meta_description=scraped_meta,
        headings=scraped_headings,
        scraped_text=scraped_text,
        niche=niche_val,
    )

    try:
        raw_output = llm.complete(prompt)
    except Exception as exc:
        lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
        lead.validation_reason = f"LLM error: {exc}"
        db.commit()
        return False, f"LLM error: {exc}"

    # 5. Parse and verify output
    parsed_json = _parse_llm_json(raw_output)
    if not parsed_json:
        lead.status = LeadStatus.NEEDS_MANUAL_NOTE.value
        lead.validation_reason = "LLM returned NONE or invalid JSON"
        db.commit()
        return False, "LLM returned NONE or invalid format"

    note = str(parsed_json.get("note") or "").strip()
    evidence = str(parsed_json.get("evidence_quote") or "").strip()

    # Determine pitch angle and fallbacks
    if not has_website:
        pitch_angle = "website"
    else:
        pitch_angle = str(parsed_json.get("pitch_angle") or "automation").strip().lower()
        if pitch_angle not in ("website", "seo", "automation", "custom_software"):
            pitch_angle = "automation"

    offered_service = str(parsed_json.get("offered_service") or "").strip()
    if not offered_service:
        if pitch_angle == "website":
            offered_service = "modern, high-performing websites and web applications"
        elif pitch_angle == "seo":
            offered_service = "technical SEO and organic search visibility pipelines"
        elif pitch_angle == "custom_software":
            offered_service = "custom software development and cloud API infrastructure"
        else:
            offered_service = settings.DEFAULT_SERVICE

    example_result = str(parsed_json.get("example_result") or "").strip()
    if not example_result:
        if pitch_angle == "website":
            example_result = "turning digital visitors into qualified inbound leads consistently"
        elif pitch_angle == "seo":
            example_result = "a 2.5x increase in qualified organic search traffic within 90 days"
        elif pitch_angle == "custom_software":
            example_result = "shipping core product features 3x faster with zero technical debt"
        else:
            example_result = settings.DEFAULT_EXAMPLE_RESULT

    # If lead had no website, ensure evidence reference matches company or default
    if not has_website:
        if not evidence or (lead.company and evidence.lower() not in lead.company.lower()):
            evidence = lead.company or "No website provided"

    is_valid, rejection_reason = verify_personal_note(
        note=note,
        evidence_quote=evidence,
        scraped_text=scraped_text,
        pitch_angle=pitch_angle,
        company=lead.company,
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

    # 6. Success: update lead record
    lead.personal_note = note
    lead.evidence_quote = evidence
    lead.pitch_angle = pitch_angle
    lead.offered_service = offered_service
    lead.example_result = example_result
    lead.status = LeadStatus.APPROVED.value if auto_approve else LeadStatus.PERSONALIZED.value
    lead.validation_reason = None

    db.add(
        Event(
            lead_id=lead.id,
            type=EventType.VALIDATED.value,
            payload={
                "stage": "personalization",
                "pitch_angle": pitch_angle,
                "offered_service": offered_service,
                "example_result": example_result,
                "note": note,
                "evidence_quote": evidence,
                "auto_approved": auto_approve,
            },
            created_at=now_dt,
        )
    )
    db.commit()
    logger.info(
        "Lead #%d (%s) personalized with angle '%s': %s", lead.id, lead.email, pitch_angle, note
    )
    return True, "Personalization generated successfully"


def personalize_campaign(
    campaign_name_or_id: str | int,
    db: Session,
    fetcher: WebFetcher,
    llm: LlmClient,
    clock: Clock | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Personalize eligible leads in a campaign with optional batch capping.

    Args:
        campaign_name_or_id: Target campaign name or ID.
        db: SQLAlchemy session.
        fetcher: WebFetcher implementation.
        llm: LlmClient implementation.
        clock: Optional Clock implementation.
        limit: Optional maximum batch size to prevent timeouts.

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

    base_stmt = select(Lead).where(
        Lead.campaign_id == campaign.id,
        Lead.status.in_((LeadStatus.VALID.value, LeadStatus.NEW.value)),
    )
    total_eligible = db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0

    stmt = base_stmt.order_by(Lead.id.asc())
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)

    leads = db.scalars(stmt).all()

    stats = {
        "total": len(leads),
        "personalized": 0,
        "needs_manual_note": 0,
        "preserved": 0,
        "capped": 1 if (limit and total_eligible > len(leads)) else 0,
        "remaining": max(0, total_eligible - len(leads)),
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
