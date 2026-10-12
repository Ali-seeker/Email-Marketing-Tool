# ColdReach - System Rules & Constraints

## Non-Negotiable Architecture & Safety Rules

1. **SAFE BY DEFAULT (Dry-Run)**:
   - The setting `LIVE_MODE` defaults to `false`.
   - While `LIVE_MODE=false`, NO real email may ever leave the host machine under any circumstance.
   - In dry-run mode, the sender must only write to a local log and/or outbox database table.
   - Real outbound SMTP dispatch is strictly prohibited unless `LIVE_MODE=true` is explicitly set by a human in `.env`.

2. **Zero Hardcoded Secrets**:
   - Secrets (SMTP/IMAP passwords, API keys, credentials) live only in `.env`.
   - Never hardcode, never log, and never commit secrets to version control.
   - Maintain `.env.example` with safe placeholder keys and descriptions.

3. **Injectable Clock for All Time Logic**:
   - All time-dependent logic (scheduling, cooldowns, daily quotas, intervals) MUST use the injectable `Clock` (`app.core.clock.Clock`).
   - Never call `datetime.now()` or `datetime.utcnow()` directly in business services or workers.
   - Unit and integration tests must control and advance time deterministically using `FakeClock` / `FrozenClock`.

4. **Strict Interface Isolation for External Systems**:
   - Every external system (SMTP, IMAP, web scraping, DNS lookups, LLMs) MUST be defined behind an interface (`abc.ABC`).
   - Implement both a production provider and an in-memory/fake provider for test harnesses.
   - Automated tests must NEVER touch the public internet, real mail exchangers, or real remote mailboxes.

5. **Type Hints, Docstrings, and Unit Testing**:
   - Every service function must be fully type-hinted and documented with descriptive docstrings.
   - Every service function must have unit tests covering normal execution and edge/failure cases.

6. **Hard Safety Limits in Configuration**:
   - Config-driven safety throttles with conservative defaults:
     - `MAX_EMAILS_PER_DAY_PER_MAILBOX`: Maximum 30 emails/day per mailbox.
     - `MIN_SECONDS_BETWEEN_EMAILS`: Minimum 120 seconds between consecutive dispatches.
     - `BOUNCE_RATE_STOP_THRESHOLD_PERCENT`: Immediate campaign pause if mailbox bounce rate reaches or exceeds 2.0%.

7. **Strict Plain-Text Email Standard**:
   - Outbound emails are plain-text only (`text/plain`).
   - No tracking pixels, no spy scripts, no link shorteners, and no hidden redirects.
