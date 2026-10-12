"""Templates, sequence definitions, and spam linter preview API endpoints."""

from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import (
    SequenceStepResponse,
    SequenceYamlRequest,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
)
from app.models.campaign import Campaign
from app.models.lead import Lead, LeadStatus
from app.models.sequence import SequenceStep
from app.services.linter import lint_email
from app.services.templates import (
    build_template_context,
    load_sequence_from_yaml,
    render_step_email,
    render_template_string,
)

router = APIRouter(prefix="/templates", tags=["Templates"])


@router.get("/sequence")
def get_sequence_steps(
    campaign_id: Annotated[int, Query(description="Campaign ID")],
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Retrieve sequence step definitions and raw YAML representation for a campaign."""
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found.",
        )

    steps = db.scalars(
        select(SequenceStep)
        .where(SequenceStep.campaign_id == campaign.id)
        .order_by(SequenceStep.step.asc())
    ).all()

    steps_data = [
        {
            "step": s.step,
            "delay_days": s.delay_days,
            "send_as_reply": s.send_as_reply,
            "subjects": s.subjects,
            "bodies": s.bodies,
        }
        for s in steps
    ]

    yaml_text = yaml.safe_dump({"steps": steps_data}, sort_keys=False)

    return {
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "steps": [SequenceStepResponse(**sd) for sd in steps_data],
        "yaml_content": yaml_text,
    }


@router.put("/sequence")
def update_sequence_steps(
    payload: SequenceYamlRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Update and re-validate sequence step definitions from YAML string."""
    campaign = db.get(Campaign, payload.campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {payload.campaign_id} not found.",
        )

    try:
        created_steps = load_sequence_from_yaml(
            campaign_id=campaign.id,
            yaml_source=payload.yaml_content,
            db=db,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sequence YAML definition: {exc}",
        ) from exc

    return {
        "campaign_id": campaign.id,
        "updated_steps_count": len(created_steps),
        "status": "success",
    }


@router.post("/preview", response_model=TemplatePreviewResponse)
def preview_email_template(
    payload: TemplatePreviewRequest,
    db: Session = Depends(get_db),
) -> TemplatePreviewResponse:
    """Render an outreach email step and evaluate with spam linter."""
    # 1. Resolve lead entity or mock
    if payload.lead_id:
        lead = db.get(Lead, payload.lead_id)
        if not lead:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Lead {payload.lead_id} not found.",
            )
        campaign = lead.campaign or (
            db.get(Campaign, lead.campaign_id) if lead.campaign_id else None
        )
    else:
        campaign = (
            db.get(Campaign, payload.campaign_id)
            if payload.campaign_id
            else db.scalar(select(Campaign).limit(1))
        )
        if not campaign:
            campaign = Campaign(name="Preview Campaign", niche="AI automation")

        lead = Lead(
            campaign_id=campaign.id if campaign else 1,
            email="sarah.connor@example.com",
            first_name="Sarah",
            last_name="Connor",
            company="Cyberdyne Systems",
            website="https://cyberdyne.example.com",
            personal_note="I saw your automated PostgreSQL disaster recovery pipeline.",
            status=LeadStatus.VALID.value,
        )

    # 2. Render email
    if payload.custom_subject and payload.custom_body:
        ctx = build_template_context(lead, campaign)
        rendered_sub = render_template_string(payload.custom_subject, ctx)
        rendered_body = render_template_string(payload.custom_body, ctx)
        lint_res = lint_email(subject=rendered_sub, body=rendered_body, step=payload.step)
        score = (
            100
            if lint_res.passed
            else max(0, 100 - len(lint_res.failures) * 25 - len(lint_res.warnings) * 10)
        )
        return TemplatePreviewResponse(
            subject=rendered_sub,
            body=rendered_body,
            linter_passed=lint_res.passed,
            linter_score=score,
            linter_failures=lint_res.failures,
            linter_warnings=lint_res.warnings,
            is_ready=lint_res.passed,
        )

    try:
        rendered = render_step_email(lead=lead, step_num=payload.step, db=db)
        r_score = (
            100
            if rendered.lint_result.passed
            else max(
                0,
                100
                - len(rendered.lint_result.failures) * 25
                - len(rendered.lint_result.warnings) * 10,
            )
        )
        return TemplatePreviewResponse(
            subject=rendered.subject,
            body=rendered.body,
            linter_passed=rendered.lint_result.passed,
            linter_score=r_score,
            linter_failures=rendered.lint_result.failures,
            linter_warnings=rendered.lint_result.warnings,
            is_ready=rendered.is_ready,
            error=rendered.error,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to render email: {exc}",
        ) from exc
