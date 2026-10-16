"""Template rendering engine and sequence management service.

Implements Jinja2 rendering with StrictUndefined, deterministic and balanced variant
assignment per lead, thread subject resolution ('Re: ...'), signature and opt-out
assembly, and spam linter verification.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jinja2
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.campaign import Campaign
from app.models.lead import Lead
from app.models.sequence import SequenceStep
from app.models.setting import Setting
from app.services.linter import LintResult, lint_email

logger = get_logger(__name__)

# Jinja2 environment configured with StrictUndefined to reject missing variables.
# Emails are strictly RFC 5322 plain-text, zero HTML rendering.
jinja_env = jinja2.Environment(
    undefined=jinja2.StrictUndefined,
    autoescape=False,  # nosec B701
    trim_blocks=True,
    lstrip_blocks=True,
)


class TemplateError(Exception):
    """Base exception for template rendering errors."""

    pass


class MissingTemplateVariableError(TemplateError):
    """Raised when a template references an undefined variable."""

    pass


class MissingPersonalNoteError(TemplateError):
    """Raised when personal_note is required but absent on the lead."""

    pass


@dataclass
class RenderedEmail:
    """Consolidated outcome of rendering an email sequence step."""

    lead_id: int
    step: int
    subject: str
    body: str
    subject_variant_index: int
    body_variant_index: int
    send_as_reply: bool
    lint_result: LintResult
    is_ready: bool
    status: str
    error: str | None = None


def get_deterministic_variant_index(lead_id: int, step: int, kind: str, total_variants: int) -> int:
    """Deterministically select a variant index for a lead.

    Uses an MD5 hash of lead ID and step to ensure:
    1. The same lead always receives the exact same variant for that step.
    2. Over hundreds of leads, variant assignment is uniformly balanced (+/-15%).
    """
    if total_variants <= 1:
        return 0
    token = f"lead:{lead_id}:step:{step}:{kind}"
    digest = hashlib.md5(token.encode("utf-8"), usedforsecurity=False).hexdigest()
    return int(digest, 16) % total_variants


def load_sequence_from_yaml(
    campaign_id: int,
    yaml_source: str | Path,
    db: Session,
) -> list[SequenceStep]:
    """Load sequence steps from a YAML file or string and persist to database.

    Args:
        campaign_id: ID of the parent campaign.
        yaml_source: File path or raw YAML content string.
        db: SQLAlchemy session.

    Returns:
        List of created SequenceStep records.
    """
    if isinstance(yaml_source, (str, Path)) and Path(str(yaml_source)).is_file():
        content = Path(str(yaml_source)).read_text(encoding="utf-8")
    else:
        content = str(yaml_source)

    data = yaml.safe_load(content)
    raw_steps = data.get("steps", [])

    # Delete existing sequence steps for campaign to allow clean reload
    existing = db.scalars(select(SequenceStep).where(SequenceStep.campaign_id == campaign_id)).all()
    for s in existing:
        db.delete(s)
    db.flush()

    steps_to_create: list[SequenceStep] = []
    for step_dict in raw_steps:
        step_num = step_dict["step"]
        step_rec = SequenceStep(
            campaign_id=campaign_id,
            step=step_num,
            delay_days=step_dict.get("delay_days", 0),
            send_as_reply=step_dict.get("send_as_reply", step_num > 1),
            subjects=step_dict["subjects"],
            bodies=step_dict["bodies"],
        )
        steps_to_create.append(step_rec)

    db.add_all(steps_to_create)
    db.commit()
    return steps_to_create


def load_default_sequence(campaign_id: int, db: Session) -> list[SequenceStep]:
    """Load the bundled default 4-step sequence definition for a campaign."""
    yaml_path = (
        Path(__file__).resolve().parent.parent.parent / "config" / "sequences" / "default.yaml"
    )
    return load_sequence_from_yaml(campaign_id, yaml_path, db)


def build_signature_block() -> str:
    """Construct plain-text sender signature block from settings."""
    settings = get_settings()
    lines = [
        "",
        "--",
        settings.SENDER_NAME,
        settings.SENDER_COMPANY,
        settings.SENDER_WEBSITE,
    ]
    if settings.SENDER_ADDRESS:
        lines.append(settings.SENDER_ADDRESS)
    return "\n".join(lines)


def get_effective_require_personal_note(db: Session) -> bool:
    """Check setting REQUIRE_PERSONAL_NOTE from DB override or config."""
    db_setting = db.scalar(select(Setting).where(Setting.key == "REQUIRE_PERSONAL_NOTE"))
    if db_setting and db_setting.value:
        return db_setting.value.strip().lower() in ("true", "1", "yes")
    return get_settings().REQUIRE_PERSONAL_NOTE


def build_template_context(
    lead: Lead,
    campaign: Campaign,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble all variables required for Jinja2 rendering."""
    settings = get_settings()
    ctx = {
        "first_name": lead.first_name or "",
        "company": lead.company or "",
        "personal_note": lead.personal_note or "",
        "sender_name": settings.SENDER_NAME,
        "sender_company": settings.SENDER_COMPANY,
        "portfolio_url": settings.PORTFOLIO_URL,
        "service": (
            (lead.offered_service.strip() if getattr(lead, "offered_service", None) else None)
            or settings.DEFAULT_SERVICE
        ),
        "niche": campaign.niche or "software",
        "example_result": (
            (lead.example_result.strip() if getattr(lead, "example_result", None) else None)
            or settings.DEFAULT_EXAMPLE_RESULT
        ),
        "pitch_angle": getattr(lead, "pitch_angle", None) or "automation",
    }
    if extra_context:
        ctx.update(extra_context)
    return ctx


def render_template_string(template_str: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template string under StrictUndefined rules."""
    try:
        tmpl = jinja_env.from_string(template_str)
        return tmpl.render(**context)
    except jinja2.exceptions.UndefinedError as err:
        raise MissingTemplateVariableError(f"Missing required template variable: {err}") from err


def render_step_email(
    lead: Lead,
    step_num: int,
    db: Session,
    extra_context: dict[str, Any] | None = None,
    override_sequence_step: SequenceStep | None = None,
) -> RenderedEmail:
    """Render subject and body for a specific sequence step of a lead.

    Args:
        lead: Lead entity.
        step_num: Step number (1, 2, 3, 4).
        db: SQLAlchemy session.
        extra_context: Additional Jinja2 context variables.
        override_sequence_step: Optional in-memory SequenceStep.

    Returns:
        RenderedEmail containing subject, body, variant indexes, and linter check.
    """
    campaign = lead.campaign or db.get(Campaign, lead.campaign_id)
    if not campaign:
        raise ValueError(f"Campaign not found for lead {lead.id}")

    # Check REQUIRE_PERSONAL_NOTE requirement
    require_note = get_effective_require_personal_note(db)
    has_note = bool(lead.personal_note and lead.personal_note.strip())

    # Locate sequence step definition
    if override_sequence_step and override_sequence_step.step == step_num:
        step_def = override_sequence_step
    else:
        step_def = db.scalar(
            select(SequenceStep).where(
                SequenceStep.campaign_id == campaign.id,
                SequenceStep.step == step_num,
            )
        )
        if not step_def:
            # Auto-seed default sequence if missing
            steps = load_default_sequence(campaign.id, db)
            step_def = next((s for s in steps if s.step == step_num), None)

    if not step_def:
        raise ValueError(f"Sequence definition missing for step {step_num}")

    # Determine variants deterministically
    sub_idx = get_deterministic_variant_index(
        lead_id=lead.id,
        step=step_num,
        kind="sub",
        total_variants=len(step_def.subjects),
    )
    body_idx = get_deterministic_variant_index(
        lead_id=lead.id,
        step=step_num,
        kind="body",
        total_variants=len(step_def.bodies),
    )

    ctx = build_template_context(lead, campaign, extra_context)

    # 1. Subject line calculation
    if step_def.send_as_reply and step_num > 1:
        # Resolve Step 1 subject for thread reference
        step_1_def = db.scalar(
            select(SequenceStep).where(
                SequenceStep.campaign_id == campaign.id,
                SequenceStep.step == 1,
            )
        )
        if step_1_def and step_1_def.subjects:
            step_1_sub_idx = get_deterministic_variant_index(
                lead_id=lead.id,
                step=1,
                kind="sub",
                total_variants=len(step_1_def.subjects),
            )
            raw_step_1_sub = step_1_def.subjects[step_1_sub_idx]
            rendered_step_1_sub = render_template_string(raw_step_1_sub, ctx)
            cleaned_sub = rendered_step_1_sub.strip()
            if cleaned_sub.lower().startswith("re:"):
                rendered_subject = cleaned_sub
            else:
                rendered_subject = f"Re: {cleaned_sub}"
        else:
            raw_sub = step_def.subjects[sub_idx]
    else:
        raw_sub = step_def.subjects[sub_idx]
        rendered_subject = render_template_string(raw_sub, ctx)

    rendered_subject = re.sub(r"[\r\n]+", " ", rendered_subject).strip()

    # 2. Body rendering
    raw_body = step_def.bodies[body_idx]
    rendered_body = render_template_string(raw_body, ctx).strip()

    # Append signature block
    rendered_body = f"{rendered_body}\n{build_signature_block()}"

    # 3. Lint check
    lint_res = lint_email(subject=rendered_subject, body=rendered_body, step=step_num)

    # 4. Personal note validation
    is_ready = lint_res.passed
    status = "approved" if is_ready else "draft"
    error = None

    if require_note and not has_note:
        is_ready = False
        status = "draft"
        error = "missing personal note"
    elif not lint_res.passed:
        status = "draft"
        error = f"Linter failed: {'; '.join(lint_res.failures)}"

    return RenderedEmail(
        lead_id=lead.id,
        step=step_num,
        subject=rendered_subject,
        body=rendered_body,
        subject_variant_index=sub_idx,
        body_variant_index=body_idx,
        send_as_reply=step_def.send_as_reply,
        lint_result=lint_res,
        is_ready=is_ready,
        status=status,
        error=error,
    )
