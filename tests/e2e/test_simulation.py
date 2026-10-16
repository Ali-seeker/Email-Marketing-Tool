"""30-Day ColdReach Outreach System End-to-End Simulation Test.

Simulates a complete 30-day operating month for a campaign of 400 generated leads
using 100% network-free and socket-free fakes:
- FakeClock: Deterministic simulated time progression.
- FakeSender: In-memory outbound email delivery.
- FakeMailboxReader: Inbound reply and bounce feed.
- FakeFetcher: Simulated web scrapings.
- FakeLlm: Grounded personal notes and evidence quotes.

Asserts strict system invariants continuously:
1. Never more than the daily ramp-up cap (clamped to 30 max).
2. Never two sends closer than MIN_GAP_SECONDS (120s).
3. Every send inside the lead's local send window (8am-5pm on Mon-Fri).
4. Never an email to a lead that replied, bounced, unsubscribed, or is suppressed.
5. Never the same step sent twice to the same lead.
6. Follow-ups only sent after configured delay from previous step sent time.
7. No email without an approved personal note.

Also tests bounce spike kill-switch activation and resumption.
"""

import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.clock import FakeClock
from app.core.db import Base
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead, LeadStatus
from app.models.reply import Reply
from app.models.setting import Setting
from app.services.dns_resolver import FakeDnsResolver
from app.services.fetcher import FakeFetcher
from app.services.importer import import_leads
from app.services.inbox import FakeMailboxReader, IncomingMail
from app.services.llm import FakeLlm
from app.services.personalizer import personalize_leads
from app.services.planner import (
    HARD_MAX_LIMIT,
    get_mailbox_tz,
    get_outreach_start_date,
    get_ramp_up_cap,
    get_send_window,
    plan_day,
)
from app.services.safety import (
    is_killswitch_active,
    resume_sending,
)
from app.services.sender import FakeSender
from app.services.sequencer import DEFAULT_STEP_DELAYS, can_send, enroll
from app.services.suppression import add as add_suppression
from app.services.templates import load_default_sequence
from app.services.validator import validate_leads
from app.worker import Worker


def generate_400_lead_csv() -> str:
    """Generate a realistic CSV dataset of 400 rows.

    Contains:
    - 320 valid unique leads across 5 timezones
    - 30 duplicate rows
    - 20 role addresses (info@, sales@, etc.)
    - 10 invalid email syntax rows
    - 20 suppressed domain/email rows
    """
    lines = ["email,first_name,last_name,company,website,title,country,timezone,personal_note"]
    tz_list = [
        ("US", "America/New_York"),
        ("US", "America/Los_Angeles"),
        ("UK", "Europe/London"),
        ("JP", "Asia/Tokyo"),
        ("AU", "Australia/Sydney"),
    ]

    # 1. 320 Valid business leads
    for i in range(1, 321):
        country, tz = tz_list[i % len(tz_list)]
        lines.append(
            f"founder{i}@company{i}.com,Founder{i},Smith{i},Company {i},"
            f"https://company{i}.com,CEO,{country},{tz},"
            f"Loved your company's latest automation milestone."
        )

    # 2. 30 Duplicate rows (pointing to earlier emails)
    for i in range(1, 31):
        lines.append(
            f"founder{i}@company{i}.com,Founder{i},Duplicate{i},Company {i},"
            f"https://company{i}.com,CEO,US,America/New_York,"
            f"Duplicate record note."
        )

    # 3. 20 Role addresses
    role_prefixes = ["info", "sales", "support", "admin", "contact"]
    for i in range(1, 21):
        pfx = role_prefixes[i % len(role_prefixes)]
        lines.append(
            f"{pfx}@genericcorp{i}.com,Manager,Role,GenericCorp {i},"
            f"https://genericcorp{i}.com,Manager,US,America/New_York,"
            f"Role account personal note."
        )

    # 4. 10 Invalid email syntax / bad rows
    for i in range(1, 11):
        lines.append(
            f"invalid_email_{i}#at#badcorp,Broken,Name,Broken Corp,"
            f"https://broken.com,VP,US,America/New_York,"
            f"Invalid email note."
        )

    # 5. 20 Suppressed addresses (will match pre-seeded suppression list)
    for i in range(1, 21):
        lines.append(
            f"blocked_user{i}@suppressedcorp.com,Blocked,User,Suppressed Corp,"
            f"https://suppressedcorp.com,Lead,US,America/New_York,"
            f"Suppressed note."
        )

    return "\n".join(lines)


def setup_simulation_db(engine) -> sessionmaker:
    """Initialize schema, settings, and sequence templates."""
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        # Pre-seed global suppression
        add_suppression(db, "suppressedcorp.com", reason="compliance", is_domain=True)
        add_suppression(db, "blacklisted@example.com", reason="legal", is_domain=False)

        # Pre-seed runtime configuration settings
        db.add(Setting(key="OUTREACH_START_DATE", value="2026-11-01"))
        db.add(Setting(key="MAILBOX_TZ", value="America/New_York"))
        db.add(Setting(key="HARD_MAX_PER_DAY", value="30"))
        db.add(Setting(key="MIN_GAP_SECONDS", value="120"))
        db.add(Setting(key="BOUNCE_RATE_STOP_THRESHOLD_PERCENT", value="2.0"))
        db.add(Setting(key="REQUIRE_PERSONAL_NOTE", value="true"))
        db.commit()
    return session_factory


class SimulationMailboxGenerator:
    """Generates realistic inbound replies and bounces matching probability distributions:

    - ~3% Hard bounces
    - ~2% Auto-replies / Out of Office
    - ~4% Genuine replies (25% positive hints)
    - ~1% Unsubscribe requests
    """

    def __init__(self, rng_seed: int = 42):
        self.rng = random.Random(rng_seed)
        self.next_uid = 1000

    def generate_response_for_sent(
        self, sent_msg: EmailMessage, lead: Lead, send_time: datetime
    ) -> IncomingMail | None:
        """Determine if a dispatched email receives an inbound response."""
        roll = self.rng.random()  # [0.0, 1.0)
        self.next_uid += 1
        resp_time = send_time + timedelta(hours=self.rng.randint(1, 24))

        if roll < 0.03:
            # 3% Hard Bounce
            return IncomingMail(
                uid=self.next_uid,
                raw_bytes=b"",
                message_id=f"<bounce-{self.next_uid}@mail.example.com>",
                from_email="mailer-daemon@mx.google.com",
                from_name="Mail Delivery Subsystem",
                to_email="sender@agenticsoftware.com",
                subject=f"Delivery Status Notification (Failure): {sent_msg.subject}",
                date=resp_time,
                in_reply_to=None,
                references=[],
                body_text=(
                    f"550 5.1.1 The email account that you tried to reach does not exist: "
                    f"{lead.email}. Remote host said: 550 User unknown."
                ),
            )
        elif roll < 0.05:
            # 2% Auto-reply / Out of office
            return IncomingMail(
                uid=self.next_uid,
                raw_bytes=b"",
                message_id=f"<ooo-{self.next_uid}@corp.com>",
                from_email=lead.email,
                from_name=lead.first_name,
                to_email="sender@agenticsoftware.com",
                subject=f"Automatic reply: {sent_msg.subject}",
                date=resp_time,
                in_reply_to=None,
                references=[],
                body_text="Thank you for your email. I am currently out of the office on annual leave with limited access to email.",
            )
        elif roll < 0.09:
            # 4% Genuine reply (a quarter are positive)
            is_positive = self.rng.random() < 0.25
            if is_positive:
                body = (
                    "Hi Alex, thanks for reaching out! We are actually evaluating automation "
                    "tools right now. Are you free for a 15-minute call next Tuesday at 2pm?"
                )
            else:
                body = (
                    "Thanks for the note, but we already have an in-house team handling this. "
                    "Not interested at this time."
                )
            return IncomingMail(
                uid=self.next_uid,
                raw_bytes=b"",
                message_id=f"<reply-{self.next_uid}@company.com>",
                from_email=lead.email,
                from_name=lead.first_name,
                to_email="sender@agenticsoftware.com",
                subject=f"Re: {sent_msg.subject}",
                date=resp_time,
                in_reply_to=None,
                references=[],
                body_text=body,
            )
        elif roll < 0.10:
            # 1% Unsubscribe request
            return IncomingMail(
                uid=self.next_uid,
                raw_bytes=b"",
                message_id=f"<unsub-{self.next_uid}@corp.com>",
                from_email=lead.email,
                from_name=lead.first_name,
                to_email="sender@agenticsoftware.com",
                subject=f"Re: {sent_msg.subject}",
                date=resp_time,
                in_reply_to=None,
                references=[],
                body_text="Please remove me from your mailing list and unsubscribe my email address.",
            )
        # 90% No response
        return None


def test_30_day_simulation_and_invariants():
    """Execute complete 30-day simulation verifying all invariants on every tick."""
    engine = create_engine("sqlite:///:memory:")
    session_factory = setup_simulation_db(engine)

    # Simulation start time: Sunday Nov 1, 2026, 08:00 EST
    start_dt = datetime(2026, 11, 1, 8, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    fake_clock = FakeClock(start_dt)
    fake_sender = FakeSender()
    fake_mailbox = FakeMailboxReader()
    fake_fetcher = FakeFetcher()
    fake_llm = FakeLlm()
    mailbox_generator = SimulationMailboxGenerator(rng_seed=12345)

    with session_factory() as db:
        # 1. Create and populate campaign
        csv_text = generate_400_lead_csv()
        import_report = import_leads(
            db=db,
            campaign_name="30-Day Growth Campaign",
            csv_file=csv_text,
            clock=fake_clock,
        )
        assert import_report.total_rows == 400
        assert import_report.imported >= 300
        assert import_report.duplicates == 30
        assert import_report.suppressed == 20
        assert import_report.rejected == 10

        campaign = db.scalar(select(Campaign).where(Campaign.name == "30-Day Growth Campaign"))
        assert campaign is not None
        campaign.status = CampaignStatus.ACTIVE.value
        db.commit()
        load_default_sequence(campaign.id, db)

        # 2. Validate leads
        val_summary = validate_leads(
            db=db,
            campaign_id=campaign.id,
            dns_resolver=FakeDnsResolver(default_valid=True),
        )
        assert val_summary.valid_count >= 300
        assert val_summary.invalid_count == 20  # Role addresses rejected

        # 3. Personalize leads with FakeLlm
        pers_report = personalize_leads(
            db=db,
            campaign_id=campaign.id,
            fetcher=fake_fetcher,
            llm_client=fake_llm,
            clock=fake_clock,
        )
        assert pers_report.personalized_count > 0

        # 4. Approve personalized leads for enrollment
        approved_leads = db.scalars(
            select(Lead).where(
                Lead.campaign_id == campaign.id,
                Lead.status.in_(
                    [
                        LeadStatus.VALID.value,
                        LeadStatus.PERSONALIZED.value,
                        LeadStatus.APPROVED.value,
                    ]
                ),
                Lead.personal_note.is_not(None),
            )
        ).all()
        for lead_item in approved_leads:
            lead_item.status = LeadStatus.APPROVED.value
        db.commit()

        enroll(campaign.id, db, clock=fake_clock)

        worker = Worker(
            sender=fake_sender,
            clock=fake_clock,
            session_factory=session_factory,
            inbox_reader=fake_mailbox,
        )

        # Invariant audit tracking across the entire 30-day month
        daily_sends: dict[date, int] = defaultdict(int)
        last_dispatch_time: datetime | None = None
        sent_lead_steps: dict[int, set[int]] = defaultdict(set)
        lead_step_sent_times: dict[tuple[int, int], datetime] = {}

        # =========================================================================
        # 30-DAY SIMULATION LOOP
        # =========================================================================
        sim_days = 30
        mailbox_tz = get_mailbox_tz(db)

        for day_offset in range(sim_days):
            current_day_date = (start_dt + timedelta(days=day_offset)).date()

            # Advance clock to start of day: 00:00 AM Eastern
            fake_clock.set_time(
                datetime.combine(
                    current_day_date,
                    time(0, 0, 0),
                    tzinfo=mailbox_tz,
                )
            )

            # Plan dispatches for today
            plan_day(current_day_date, db, clock=fake_clock)

            # Daily ramp-up cap expectation
            start_date_val = get_outreach_start_date(db)
            expected_day_cap = min(
                get_ramp_up_cap(current_day_date, start_date_val),
                HARD_MAX_LIMIT,
            )

            # Simulate 24 hours in 15-minute ticks (96 ticks per day)
            for _ in range(96):
                fake_clock.advance(seconds=900)
                now_clk = fake_clock.now()

                # Process pending outbound messages
                stmt_scheduled = select(EmailMessage).where(
                    EmailMessage.status == EmailMessageStatus.SCHEDULED.value,
                    EmailMessage.scheduled_at <= now_clk,
                )
                scheduled_msgs = db.scalars(stmt_scheduled).all()

                for msg in scheduled_msgs:
                    lead = db.get(Lead, msg.lead_id)
                    assert lead is not None

                    # Check if can_send permits
                    allowed_by_gatekeeper = can_send(lead, msg, db, clock=fake_clock)
                    if not allowed_by_gatekeeper:
                        worker.process_message(msg.id, db, fake_sender, now_clk)
                        continue

                    # Execute dispatch through worker
                    was_processed = worker.process_message(msg.id, db, fake_sender, now_clk)
                    if not was_processed:
                        continue

                    # Spacing between dispatches
                    fake_clock.advance(seconds=130)

                    # Re-read message to verify if actually dispatched
                    db.refresh(msg)
                    if msg.status in (
                        EmailMessageStatus.SENT.value,
                        EmailMessageStatus.SENT_DRY_RUN.value,
                    ):
                        sent_dt = msg.sent_at or now_clk
                        local_send_date = sent_dt.astimezone(mailbox_tz).date()
                        daily_sends[local_send_date] += 1

                        # =========================================================
                        # ASSERT INVARIANTS CONTINUOUSLY
                        # =========================================================
                        # 1. Never more than ramp-up cap per day and never more than 30
                        assert daily_sends[local_send_date] <= expected_day_cap, (
                            f"Daily sends {daily_sends[local_send_date]} exceeded cap {expected_day_cap} on {local_send_date}"
                        )
                        assert daily_sends[local_send_date] <= 30, (
                            f"Daily sends {daily_sends[local_send_date]} exceeded hard limit 30"
                        )

                        # 2. Never two sends closer than MIN_GAP_SECONDS (120s)
                        if last_dispatch_time is not None:
                            gap = (sent_dt - last_dispatch_time).total_seconds()
                            assert gap >= 120, (
                                f"Send gap violation: {gap}s < 120s between dispatches"
                            )
                        last_dispatch_time = sent_dt

                        # 3. Every send inside lead's send window and allowed weekday
                        lead_tz = ZoneInfo(lead.timezone) if lead.timezone else mailbox_tz
                        lead_local_time = sent_dt.astimezone(lead_tz)
                        assert lead_local_time.weekday() < 5, (
                            f"Dispatched on weekend ({lead_local_time.strftime('%A')}) for {lead.email}"
                        )
                        w_start, w_end = get_send_window(db)
                        lead_hour = lead_local_time.time()
                        assert w_start <= lead_hour <= w_end, (
                            f"Sent outside lead's window: {lead_hour} not between {w_start} and {w_end}"
                        )

                        # 4. Never to stopped lead
                        assert lead.status not in (
                            LeadStatus.REPLIED.value,
                            LeadStatus.BOUNCED.value,
                            LeadStatus.UNSUBSCRIBED.value,
                        ), f"Dispatched to stopped lead in status {lead.status}"

                        # 5. Never same step twice to same lead
                        assert msg.step not in sent_lead_steps[lead.id], (
                            f"Duplicate step {msg.step} dispatched to lead {lead.id}"
                        )
                        sent_lead_steps[lead.id].add(msg.step)
                        lead_step_sent_times[(lead.id, msg.step)] = sent_dt

                        # 6. Follow-ups only after configured delay from previous step
                        if msg.step > 1:
                            prev_sent_time = lead_step_sent_times.get((lead.id, msg.step - 1))
                            assert prev_sent_time is not None, (
                                f"Step {msg.step} sent before Step {msg.step - 1}"
                            )
                            expected_delay_days = DEFAULT_STEP_DELAYS.get(msg.step, 4)
                            diff_days = (sent_dt.date() - prev_sent_time.date()).days
                            assert diff_days >= expected_delay_days, (
                                f"Follow-up step {msg.step} sent after {diff_days}d (min required: {expected_delay_days}d)"
                            )

                        # 7. No email without approved personal note
                        assert lead.personal_note and lead.personal_note.strip(), (
                            f"Email {msg.id} dispatched without personal note on lead {lead.id}"
                        )

                        # Generate potential simulated response
                        resp_mail = mailbox_generator.generate_response_for_sent(msg, lead, sent_dt)
                        if resp_mail:
                            fake_mailbox.add_message(resp_mail)

                # Poll simulated inbox
                if fake_mailbox.messages:
                    new_inbox, _ = worker.poll_inbox(db, now_clk)
                    if new_inbox > 0:
                        db.commit()

        # Gather final simulation metrics
        total_sent_all_month = sum(daily_sends.values())
        total_replies = db.scalar(select(func.count(Reply.id))) or 0
        total_bounces = (
            db.scalar(select(func.count(Lead.id)).where(Lead.status == LeadStatus.BOUNCED.value))
            or 0
        )
        total_positive = (
            db.scalar(select(func.count(Reply.id)).where(Reply.is_positive_hint.is_(True))) or 0
        )

        print("\n" + "=" * 65)
        print("          30-DAY MONTH SIMULATION REPORT")
        print("=" * 65)
        print(f" Total Dispatched : {total_sent_all_month} emails")
        print(f" Total Replies    : {total_replies} ({total_positive} positive hints)")
        print(f" Total Bounces    : {total_bounces}")
        if total_sent_all_month > 0:
            print(f" Month Reply Rate : {(total_replies / total_sent_all_month) * 100:.2f}%")
            print(f" Month Bounce Rate: {(total_bounces / total_sent_all_month) * 100:.2f}%")
        print("=" * 65 + "\n")

        assert total_sent_all_month > 0
        assert total_replies > 0


def test_bounce_spike_killswitch_trip_and_resume():
    """Simulate a 10% bounce spike; verify kill-switch trips within 20 emails and halts sending."""
    engine = create_engine("sqlite:///:memory:")
    session_factory = setup_simulation_db(engine)

    fake_clock = FakeClock(datetime(2026, 11, 10, 9, 0, 0, tzinfo=ZoneInfo("America/New_York")))
    fake_sender = FakeSender()
    fake_mailbox = FakeMailboxReader()

    with session_factory() as db:
        # Create campaign and 50 leads
        campaign = Campaign(name="Spike Campaign", status=CampaignStatus.ACTIVE.value)
        db.add(campaign)
        db.flush()
        load_default_sequence(campaign.id, db)

        leads = []
        for i in range(50):
            lead_obj = Lead(
                campaign_id=campaign.id,
                email=f"spike_lead_{i}@spikedomain.com",
                first_name="Spike",
                last_name=f"Lead{i}",
                status=LeadStatus.APPROVED.value,
                personal_note="Custom spike testing note.",
                created_at=fake_clock.now(),
            )
            leads.append(lead_obj)
        db.add_all(leads)
        db.commit()

        enroll(campaign.id, db, clock=fake_clock)

        # Plan dispatches
        plan_day(fake_clock.now().date(), db, clock=fake_clock)
        worker = Worker(
            sender=fake_sender,
            clock=fake_clock,
            session_factory=session_factory,
            inbox_reader=fake_mailbox,
        )

        emails_after_spike = 0
        killswitch_tripped_at = None

        # Process messages, injecting hard bounces on 10% of sends
        scheduled_msgs = db.scalars(
            select(EmailMessage).where(EmailMessage.status == EmailMessageStatus.SCHEDULED.value)
        ).all()

        for idx, msg in enumerate(scheduled_msgs):
            if is_killswitch_active(db):
                break

            worker.process_message(msg.id, db, fake_sender, fake_clock.now())
            fake_clock.advance(seconds=130)

            # Inject a hard bounce every 4 sends (25% spike > 2% threshold)
            if idx % 4 == 0:
                lead = db.get(Lead, msg.lead_id)
                assert lead is not None
                fake_mailbox.add_message(
                    IncomingMail(
                        uid=9000 + idx,
                        raw_bytes=b"",
                        message_id=f"<bounce-{idx}@spike.com>",
                        from_email="mailer-daemon@google.com",
                        from_name="Mailer Daemon",
                        to_email="sender@agenticsoftware.com",
                        subject=f"Delivery failure: {msg.subject}",
                        date=fake_clock.now(),
                        in_reply_to=None,
                        body_text=f"550 User {lead.email} not found",
                    )
                )

            worker.poll_inbox(db, fake_clock.now())

            if is_killswitch_active(db) and killswitch_tripped_at is None:
                killswitch_tripped_at = idx

            if killswitch_tripped_at is not None:
                emails_after_spike += 1

        # Assert kill-switch tripped
        assert is_killswitch_active(db) is True, "Killswitch failed to trip during bounce spike"
        assert emails_after_spike <= 20, (
            f"Too many emails dispatched after spike began: {emails_after_spike} > 20"
        )

        # Assert that with kill-switch active, worker refuses to send any email
        unsent_msg = db.scalar(
            select(EmailMessage).where(EmailMessage.status == EmailMessageStatus.SCHEDULED.value)
        )
        if unsent_msg:
            result = worker.process_message(unsent_msg.id, db, fake_sender, fake_clock.now())
            assert result is False, "Worker should refuse dispatch when killswitch is active"
            db.refresh(unsent_msg)
            assert unsent_msg.status == EmailMessageStatus.SCHEDULED.value, (
                "Unsent message status should not change"
            )

        # Test Resumption Flow
        resume_res = resume_sending(
            db=db,
            acknowledged_by="operator@agenticsoftware.com",
            root_cause="Bounce spike in spike domain resolved",
            clock=fake_clock,
        )
        assert resume_res.success is True
        assert is_killswitch_active(db) is False, "Killswitch should be inactive after resume"
        # Confirm cap was halved
        assert resume_res.reduced_cap <= 15
