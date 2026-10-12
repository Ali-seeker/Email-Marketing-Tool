"""Demo data seeder for ColdReach operator dashboard testing.

Generates realistic demo state:
- 1 Active Campaign with 4 sequence steps
- 300 Fake leads across all lifecycle statuses
- 40 Pending approval drafts with evidence quotes
- 30 Days of realistic timeseries events, emails, and replies
- Warning or critical health metrics and killswitch configuration
All emails strictly use the @example.com domain.
"""

import random
from datetime import UTC, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.clock import system_clock
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus, RiskLevel
from app.models.reply import Reply
from app.models.sequence import SequenceStep
from app.models.setting import Setting
from app.services.templates import load_default_sequence

FIRST_NAMES = [
    "Alex",
    "Jordan",
    "Taylor",
    "Morgan",
    "Sam",
    "Chris",
    "Pat",
    "Robin",
    "Casey",
    "Jamie",
    "Riley",
    "Cameron",
    "Avery",
    "Dakota",
    "Reese",
    "Elena",
    "Marcus",
    "Sophie",
    "Liam",
    "Emma",
    "Noah",
    "Olivia",
    "William",
    "Ava",
    "James",
    "Isabella",
    "Lucas",
    "Mia",
    "Henry",
    "Charlotte",
]

LAST_NAMES = [
    "Chen",
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Wilson",
    "Anderson",
    "Thomas",
    "Taylor",
    "Moore",
    "Jackson",
    "Martin",
    "Lee",
    "Perez",
    "Thompson",
    "White",
    "Harris",
    "Sanchez",
    "Clark",
]

COMPANIES = [
    ("Acme Cloud", "acme.example.com", "Cloud infrastructure & orchestration"),
    ("DevPulse", "devpulse.example.com", "Developer velocity analytics"),
    ("HyperScale AI", "hyperscale.example.com", "Distributed LLM training"),
    ("DataWeave", "dataweave.example.com", "Automated ETL pipelines"),
    ("CyberShield", "cybershield.example.com", "Zero-trust identity access"),
    ("MetricFlow", "metricflow.example.com", "Real-time metrics streaming"),
    ("LogiCraft", "logicraft.example.com", "Autonomous supply chain routing"),
    ("FinStack", "finstack.example.com", "Embedded banking for platforms"),
    ("VectorOps", "vectorops.example.com", "Vector database query caching"),
    ("NovaSec", "novasec.example.com", "Cloud security posture management"),
    ("Synthetix", "synthetix.example.com", "Synthetic test data generation"),
    ("ScaleGrid", "scalegrid.example.com", "Managed multi-cloud databases"),
    ("PulsePoint", "pulsepoint.example.com", "Application health observability"),
    ("OmniChannel", "omnichannel.example.com", "B2B notification routing engine"),
    ("ZenithHR", "zenithhr.example.com", "Global payroll compliance API"),
]

TITLES = [
    "Founder & CEO",
    "Co-Founder & CTO",
    "VP of Engineering",
    "Head of Growth",
    "Director of Infrastructure",
    "Chief Product Officer",
    "Engineering Manager",
    "Head of DevOps",
    "VP of Product",
]

COUNTRIES = ["United States", "United Kingdom", "Canada", "Germany", "France"]
TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Berlin",
]

EVIDENCE_SNIPPETS = [
    "Noticed in your changelog that you recently cut API latency by 45% using Rust workers.",
    "Saw your recent podcast interview discussing zero-downtime database migrations.",
    "Loved your open-source blog post on scaling Kafka clusters across multi-region VPCs.",
    "Congratulated on your Series A announcement and recent expansion into the European market.",
    "Read your technical teardown on memory profiling in high-throughput Go microservices.",
    "Saw your LinkedIn update highlighting 100k daily active users on your dev portal.",
    "Noticed your team opened several senior distributed systems engineering roles in London.",
    "Read your case study on lowering egress networking fees by 60% with private peering.",
]


def seed_demo_database(db: Session, critical: bool = False) -> dict[str, int]:
    """Populate database with complete realistic demo data for the operator dashboard."""
    now = system_clock.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    # 1. Clean existing records for fresh demo state
    for model in (Reply, EmailMessage, Event, Lead, SequenceStep, Campaign, Setting):
        db.execute(delete(model))
    db.commit()

    # 2. Seed Settings
    outreach_start = (now - timedelta(days=30)).date().isoformat()
    db.add(Setting(key="OUTREACH_START_DATE", value=outreach_start))
    db.add(Setting(key="HARD_MAX_PER_DAY", value="30"))
    db.add(Setting(key="MAX_EMAILS_PER_DAY_PER_MAILBOX", value="30"))
    db.add(Setting(key="MIN_GAP_SECONDS", value="120"))
    db.add(Setting(key="BOUNCE_RATE_STOP_THRESHOLD_PERCENT", value="2.0"))
    db.add(Setting(key="MAILBOX_TIMEZONE", value="America/New_York"))
    db.add(Setting(key="KILLSWITCH", value="on" if critical else "off"))
    if critical:
        db.add(Setting(key="KILLSWITCH_ENGAGED_AT", value=now.isoformat()))
        db.add(
            Setting(
                key="KILLSWITCH_REASON", value="Critical bounce rate spike detected (2.4% >= 2.0%)"
            )
        )
    db.commit()

    # 3. Create Primary Campaign
    campaign = Campaign(
        name="B2B DevTools Founders Outreach",
        niche="Cloud & Developer Platforms",
        status=CampaignStatus.ACTIVE.value,
        created_at=now - timedelta(days=32),
    )
    db.add(campaign)
    db.flush()

    # 4. Create Sequence Steps
    load_default_sequence(campaign_id=campaign.id, db=db)
    db.commit()

    # 5. Generate 300 Leads across realistic status distributions:
    # - 40 personalized (pending approval review)
    # - 30 new/imported
    # - 40 valid
    # - 20 invalid
    # - 15 needs_manual_note
    # - 30 approved
    # - 65 in_sequence (active outreach)
    # - 25 replied
    # - 10 won
    # - 12 bounced
    # - 8 unsubscribed
    # - 5 paused
    target_statuses = (
        [LeadStatus.PERSONALIZED.value] * 40
        + [LeadStatus.NEW.value] * 30
        + [LeadStatus.VALID.value] * 40
        + [LeadStatus.INVALID.value] * 20
        + [LeadStatus.NEEDS_MANUAL_NOTE.value] * 15
        + [LeadStatus.APPROVED.value] * 30
        + [LeadStatus.IN_SEQUENCE.value] * 65
        + [LeadStatus.REPLIED.value] * 25
        + [LeadStatus.WON.value] * 10
        + [LeadStatus.BOUNCED.value] * 12
        + [LeadStatus.UNSUBSCRIBED.value] * 8
        + [LeadStatus.PAUSED.value] * 5
    )
    # Pad to 300 if needed
    while len(target_statuses) < 300:
        target_statuses.append(LeadStatus.IN_SEQUENCE.value)
    random.seed(42)
    random.shuffle(target_statuses)

    created_leads: list[Lead] = []
    for i, status in enumerate(target_statuses, start=1):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        comp_name, comp_domain, _ = random.choice(COMPANIES)
        handle = f"{first.lower()}.{last.lower()}{i}"
        email = f"{handle}@{comp_domain}"

        # Risk assessment
        if status == LeadStatus.INVALID.value:
            risk = RiskLevel.BAD.value
            reason = "MX lookup failed / invalid mailbox address"
        elif status == LeadStatus.BOUNCED.value:
            risk = RiskLevel.RISKY.value
            reason = "Hard 550 recipient rejected"
        else:
            risk = random.choices([RiskLevel.OK.value, RiskLevel.RISKY.value], weights=[0.9, 0.1])[
                0
            ]
            reason = "Passed syntax, domain MX, and disposable checks"

        evidence = random.choice(EVIDENCE_SNIPPETS)
        note = (
            f"Saw your team's work at {comp_name} - {evidence}"
            if status
            in (
                LeadStatus.PERSONALIZED.value,
                LeadStatus.APPROVED.value,
                LeadStatus.IN_SEQUENCE.value,
                LeadStatus.REPLIED.value,
                LeadStatus.WON.value,
            )
            else None
        )

        lead_created = now - timedelta(days=random.randint(5, 30))
        lead = Lead(
            campaign_id=campaign.id,
            email=email,
            first_name=first,
            last_name=last,
            company=comp_name,
            website=f"https://{comp_domain}",
            title=random.choice(TITLES),
            country=random.choice(COUNTRIES),
            timezone=random.choice(TIMEZONES),
            personal_note=note,
            evidence_quote=evidence if note else None,
            status=status,
            risk_level=risk,
            validation_reason=reason,
            created_at=lead_created,
            updated_at=lead_created + timedelta(hours=random.randint(1, 24)),
        )
        created_leads.append(lead)
        db.add(lead)

    db.flush()

    # 6. Generate Emails and 30 Days of History
    # Create historical events and dispatched email messages
    total_emails = 0
    total_replies = 0

    # For leads with status in_sequence, replied, won, bounced, unsubscribed:
    for lead in created_leads:
        # Import event
        db.add(
            Event(
                lead_id=lead.id,
                type=EventType.IMPORTED.value,
                payload={"email": lead.email, "company": lead.company},
                created_at=lead.created_at,
            )
        )
        # Validation event
        db.add(
            Event(
                lead_id=lead.id,
                type=EventType.VALIDATED.value,
                payload={"risk": lead.risk_level, "reason": lead.validation_reason},
                created_at=lead.created_at + timedelta(minutes=10),
            )
        )

        if lead.status in (
            LeadStatus.IN_SEQUENCE.value,
            LeadStatus.REPLIED.value,
            LeadStatus.WON.value,
            LeadStatus.BOUNCED.value,
            LeadStatus.UNSUBSCRIBED.value,
        ):
            # Step 1 email
            sent_days_ago = random.randint(1, 25)
            sent_time = now - timedelta(days=sent_days_ago, hours=random.randint(1, 8))
            e1 = EmailMessage(
                lead_id=lead.id,
                step=1,
                subject=f"Quick question for {lead.company}",
                body=(
                    f"Hi {lead.first_name},\n\n"
                    f"{lead.personal_note or 'Hope you are doing well.'}\n\n"
                    f"Would you be open to a 10-minute chat next Tuesday?\n\nBest,\nAlex"
                ),
                status=EmailMessageStatus.SENT_DRY_RUN.value,
                sent_at=sent_time,
                scheduled_at=sent_time,
            )
            db.add(e1)
            total_emails += 1

            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.SENT.value,
                    payload={"step": 1, "subject": e1.subject},
                    created_at=sent_time,
                )
            )

            # Potential follow-up step 2
            if sent_days_ago > 4 and lead.status in (
                LeadStatus.IN_SEQUENCE.value,
                LeadStatus.REPLIED.value,
            ):
                step2_time = sent_time + timedelta(days=3)
                if step2_time < now:
                    e2 = EmailMessage(
                        lead_id=lead.id,
                        step=2,
                        subject=f"Re: Quick question for {lead.company}",
                        body=(
                            f"Hi {lead.first_name},\n\n"
                            "Just floating this back to the top of your inbox.\n\nBest,\nAlex"
                        ),
                        status=EmailMessageStatus.SENT_DRY_RUN.value,
                        sent_at=step2_time,
                        scheduled_at=step2_time,
                    )
                    db.add(e2)
                    total_emails += 1
                    db.add(
                        Event(
                            lead_id=lead.id,
                            type=EventType.SENT.value,
                            payload={"step": 2, "subject": e2.subject},
                            created_at=step2_time,
                        )
                    )

            # Handle terminal outcomes
            if lead.status == LeadStatus.REPLIED.value or lead.status == LeadStatus.WON.value:
                rep_time = sent_time + timedelta(days=random.randint(1, 3))
                is_pos = lead.status == LeadStatus.WON.value or random.random() > 0.4
                classification = "positive_hint" if is_pos else "other"
                body_text = (
                    "Thanks for reaching out Alex! Let's chat next week. Send over a calendar link."
                    if is_pos
                    else "Thanks for the note, but we're not currently looking for tools right now."
                )
                rep = Reply(
                    lead_id=lead.id,
                    from_email=lead.email,
                    to_email="outreach@coldreach.local",
                    subject=f"Re: Quick question for {lead.company}",
                    body=body_text,
                    snippet=body_text[:120],
                    classification=classification,
                    is_positive_hint=is_pos,
                    created_at=rep_time,
                )
                db.add(rep)
                total_replies += 1
                db.add(
                    Event(
                        lead_id=lead.id,
                        type=EventType.REPLIED.value,
                        payload={"positive": is_pos, "classification": classification},
                        created_at=rep_time,
                    )
                )

            elif lead.status == LeadStatus.BOUNCED.value:
                bounce_time = sent_time + timedelta(minutes=15)
                db.add(
                    Event(
                        lead_id=lead.id,
                        type=EventType.BOUNCED.value,
                        payload={"code": 550, "reason": "Mailbox unavailable or user not found"},
                        created_at=bounce_time,
                    )
                )
                # Add reply record for bounce
                db.add(
                    Reply(
                        lead_id=lead.id,
                        from_email="mailer-daemon@example.com",
                        to_email="outreach@coldreach.local",
                        subject=f"Undelivered Mail Returned: Quick question for {lead.company}",
                        body="550 5.1.1 The email account that you tried to reach does not exist.",
                        snippet="550 5.1.1 Mailbox does not exist",
                        classification="bounce",
                        is_positive_hint=False,
                        created_at=bounce_time,
                    )
                )

            elif lead.status == LeadStatus.UNSUBSCRIBED.value:
                unsub_time = sent_time + timedelta(days=1)
                db.add(
                    Event(
                        lead_id=lead.id,
                        type=EventType.UNSUBSCRIBED.value,
                        payload={"reason": "User replied with unsubscribe phrase"},
                        created_at=unsub_time,
                    )
                )
                db.add(
                    Reply(
                        lead_id=lead.id,
                        from_email=lead.email,
                        to_email="outreach@coldreach.local",
                        subject=f"Re: Quick question for {lead.company}",
                        body="Please remove me from your email list.",
                        snippet="Please remove me from your email list.",
                        classification="unsubscribe",
                        is_positive_hint=False,
                        created_at=unsub_time,
                    )
                )

    # Add 4 auto-replies to provide realistic reply inbox experience
    auto_reply_leads = [ld for ld in created_leads if ld.status == LeadStatus.IN_SEQUENCE.value][:4]
    for ld in auto_reply_leads:
        ar_time = now - timedelta(days=random.randint(1, 5))
        db.add(
            Reply(
                lead_id=ld.id,
                from_email=ld.email,
                to_email="outreach@coldreach.local",
                subject=f"Automatic reply: Quick question for {ld.company}",
                body=(
                    "I am currently away at a conference with limited access to email. "
                    "I will respond upon my return."
                ),
                snippet="Automatic reply: Away at conference with limited access to email.",
                classification="auto_reply",
                is_positive_hint=False,
                created_at=ar_time,
            )
        )
        db.add(
            Event(
                lead_id=ld.id,
                type=EventType.AUTO_REPLY.value,
                payload={"reason": "Out of office automatic responder"},
                created_at=ar_time,
            )
        )

    # 7. Add Today's Scheduled Queue (for Overview Today's plan widget)
    scheduled_leads = [ld for ld in created_leads if ld.status == LeadStatus.APPROVED.value][:8]
    for idx, s_lead in enumerate(scheduled_leads):
        sched_time = now + timedelta(hours=idx + 1)
        db.add(
            EmailMessage(
                lead_id=s_lead.id,
                step=1,
                subject=f"Introduction to {s_lead.company}",
                body=f"Hi {s_lead.first_name},\n\nLooking forward to connecting.\n\nBest,\nAlex",
                status=EmailMessageStatus.SCHEDULED.value,
                scheduled_at=sched_time,
            )
        )

    # 8. Record Killswitch Event if Critical
    if critical:
        db.add(
            Event(
                lead_id=None,
                type=EventType.KILLSWITCH.value,
                payload={
                    "state": "engaged",
                    "reason": "Critical bounce rate spike detected (2.4% >= 2.0%)",
                    "bounce_rate": 2.4,
                    "threshold": 2.0,
                },
                created_at=now,
            )
        )

    db.commit()

    return {
        "campaigns": 1,
        "leads": len(created_leads),
        "emails": total_emails,
        "replies": total_replies,
        "critical": int(critical),
    }
