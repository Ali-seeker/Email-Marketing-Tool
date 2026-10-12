"""Email Validation service.

Evaluates unverified leads ('new' status) through sequential validation stages:
1. Syntax validation (RFC regex, length constraints)
2. Disposable email domain detection
3. Role-based mailbox detection
4. DNS MX / A record resolution (with database caching)
5. External verifier (Hunter.io with monthly quota)
6. Domain-to-website sanity check

Enforces strict risk policy: risky leads remain 'invalid' unless ALLOW_RISKY is enabled.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.dns_cache import DomainDnsCache
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus, RiskLevel
from app.models.setting import Setting
from app.services.dns_resolver import DnsResolver, LiveDnsResolver
from app.services.email_verifier import EmailVerifier, NullVerifier

logger = get_logger(__name__)

# Standard RFC email pattern
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

# Common role-based mailbox prefixes
ROLE_PREFIXES = frozenset(
    [
        "info",
        "support",
        "admin",
        "sales",
        "contact",
        "hello",
        "noreply",
        "no-reply",
        "billing",
        "help",
        "jobs",
        "careers",
        "team",
        "office",
        "marketing",
        "press",
    ]
)

# Common consumer / free email providers
FREE_EMAIL_PROVIDERS = frozenset(
    [
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "icloud.com",
        "aol.com",
        "zoho.com",
        "proton.me",
        "protonmail.com",
        "live.com",
        "mail.com",
        "gmx.com",
        "yandex.com",
    ]
)


def _load_disposable_domains() -> set[str]:
    """Load bundled list of disposable email domains."""
    data_file = Path(__file__).resolve().parent.parent / "data" / "disposable_domains.txt"
    domains: set[str] = set()
    if data_file.exists():
        with open(data_file, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip().lower()
                if stripped and not stripped.startswith("#"):
                    domains.add(stripped)
    return domains


DISPOSABLE_DOMAINS = _load_disposable_domains()


@dataclass
class ValidationSummary:
    """Consolidated summary of lead validation run."""

    total_checked: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    risk_ok: int = 0
    risk_risky: int = 0
    risk_bad: int = 0
    details: list[dict[str, str]] = field(default_factory=list)


def extract_domain_from_url(url: str | None) -> str | None:
    """Extract registered domain name from a URL string."""
    if not url:
        return None
    val = url.strip()
    if not val.startswith(("http://", "https://")):
        val = f"https://{val}"
    try:
        parsed = urlparse(val)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        # Remove port if present
        host = host.split(":")[0]
        return host or None
    except Exception:
        return None


def resolve_domain_dns(
    domain: str,
    db: Session,
    dns_resolver: DnsResolver,
    clock: Clock,
) -> DomainDnsCache:
    """Lookup domain DNS in cache or query and persist in database."""
    norm_domain = domain.strip().lower()
    cached = db.scalar(select(DomainDnsCache).where(DomainDnsCache.domain == norm_domain))
    if cached:
        return cached

    result = dns_resolver.resolve(norm_domain)
    entry = DomainDnsCache(
        domain=norm_domain,
        has_mx=result.has_mx,
        has_a=result.has_a,
        is_valid=result.is_valid,
        records=result.records,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    db.add(entry)
    db.flush()
    return entry


def evaluate_email(
    email: str,
    website: str | None,
    db: Session,
    dns_resolver: DnsResolver,
    verifier: EmailVerifier,
    clock: Clock,
) -> tuple[str, str]:
    """Run sequential validation checks on an email address.

    Returns:
        tuple of (risk_level, reason)
    """
    clean_email = email.strip().lower()

    # 1. Syntax check
    if not clean_email or len(clean_email) > 320 or " " in clean_email:
        return RiskLevel.BAD.value, "Malformed email syntax"

    parts = clean_email.split("@")
    if len(parts) != 2:
        return RiskLevel.BAD.value, "Malformed email syntax"

    local_part, domain = parts
    if not local_part or len(local_part) > 64 or not domain or "." not in domain:
        return RiskLevel.BAD.value, "Malformed email syntax"

    if not EMAIL_REGEX.match(clean_email):
        return RiskLevel.BAD.value, "Malformed email syntax"

    # 2. Disposable domain check
    if domain in DISPOSABLE_DOMAINS:
        return RiskLevel.BAD.value, f"Disposable email domain: {domain}"

    # Track potential risk reasons
    risk_level = RiskLevel.OK.value
    reasons: list[str] = []

    # 3. Role-based address check
    if local_part in ROLE_PREFIXES:
        risk_level = RiskLevel.RISKY.value
        reasons.append(f"Role-based mailbox ({local_part})")

    # 4. MX and A DNS record resolution
    dns_cache = resolve_domain_dns(domain, db, dns_resolver, clock)
    if not dns_cache.is_valid:
        return RiskLevel.BAD.value, f"Domain {domain} has no MX or A records"

    # 5. External verifier (Hunter.io / etc.)
    ext_result = verifier.verify(clean_email, db, clock)
    if ext_result:
        if ext_result.risk_level == RiskLevel.BAD.value:
            return RiskLevel.BAD.value, f"External verifier invalid: {ext_result.status}"
        elif ext_result.risk_level == RiskLevel.RISKY.value:
            risk_level = RiskLevel.RISKY.value
            reasons.append(f"External verifier flagged: {ext_result.status}")

    # 6. Domain-to-website sanity check
    if website:
        web_domain = extract_domain_from_url(website)
        if web_domain and domain not in FREE_EMAIL_PROVIDERS:
            # Check if domain matches website or is subdomain
            if (
                domain != web_domain
                and not web_domain.endswith(f".{domain}")
                and not domain.endswith(f".{web_domain}")
            ):
                risk_level = RiskLevel.RISKY.value
                reasons.append(f"Email domain ({domain}) does not match website ({web_domain})")

    if risk_level == RiskLevel.OK.value:
        return RiskLevel.OK.value, "Passed all validation checks"

    return risk_level, "; ".join(reasons)


def get_effective_allow_risky(db: Session) -> bool:
    """Determine whether risky leads are permitted to be marked valid."""
    db_setting = db.scalar(select(Setting).where(Setting.key == "ALLOW_RISKY"))
    if db_setting and db_setting.value:
        return db_setting.value.strip().lower() in ("true", "1", "yes")

    return get_settings().ALLOW_RISKY


def validate_leads(
    db: Session,
    campaign_name: str | None = None,
    allow_risky: bool | None = None,
    dns_resolver: DnsResolver | None = None,
    verifier: EmailVerifier | None = None,
    clock: Clock | None = None,
) -> ValidationSummary:
    """Validate all leads in status 'new' for a campaign or globally.

    Args:
        db: SQLAlchemy session.
        campaign_name: Optional campaign name filter.
        allow_risky: Override for ALLOW_RISKY setting.
        dns_resolver: Optional DNS resolver (defaults to LiveDnsResolver).
        verifier: Optional external verifier (defaults to NullVerifier).
        clock: Optional Clock (defaults to system_clock).

    Returns:
        ValidationSummary with counts and details.
    """
    active_clock = clock or system_clock
    resolver = dns_resolver or LiveDnsResolver()
    ext_verifier = verifier or NullVerifier()

    effective_allow_risky = (
        allow_risky if allow_risky is not None else get_effective_allow_risky(db)
    )

    stmt = select(Lead).where(Lead.status == LeadStatus.NEW.value)
    if campaign_name:
        stmt = stmt.join(Campaign).where(Campaign.name == campaign_name)

    leads = db.scalars(stmt).all()
    summary = ValidationSummary(total_checked=len(leads))

    now_dt = active_clock.now()

    for lead in leads:
        risk_level, reason = evaluate_email(
            email=lead.email,
            website=lead.website,
            db=db,
            dns_resolver=resolver,
            verifier=ext_verifier,
            clock=active_clock,
        )

        # Decide final status
        if risk_level == RiskLevel.OK.value:
            lead.status = LeadStatus.VALID.value
            summary.valid_count += 1
            summary.risk_ok += 1
        elif risk_level == RiskLevel.RISKY.value:
            summary.risk_risky += 1
            if effective_allow_risky:
                lead.status = LeadStatus.VALID.value
                summary.valid_count += 1
            else:
                lead.status = LeadStatus.INVALID.value
                summary.invalid_count += 1
        else:
            lead.status = LeadStatus.INVALID.value
            summary.invalid_count += 1
            summary.risk_bad += 1

        lead.risk_level = risk_level
        lead.validation_reason = reason
        lead.updated_at = now_dt

        # Audit event
        db.add(
            Event(
                lead_id=lead.id,
                type=EventType.VALIDATED.value,
                payload={
                    "email": lead.email,
                    "risk_level": risk_level,
                    "reason": reason,
                    "status": lead.status,
                },
                created_at=now_dt,
            )
        )

        summary.details.append(
            {
                "email": lead.email,
                "risk_level": risk_level,
                "reason": reason,
                "status": lead.status,
            }
        )

    db.commit()
    return summary
