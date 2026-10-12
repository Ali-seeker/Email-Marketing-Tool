# ColdReach Go-Live Checklist

This checklist defines the strict, non-negotiable verification gates required before switching ColdReach to `LIVE_MODE=true`.

> **RULE ZERO**: Safe-by-default. ColdReach defaults to `LIVE_MODE=false` (dry-run). Real emails are never sent unless `LIVE_MODE=true` is explicitly set in `.env`.

---

## Pre-Flight Gates

### Gate 1: DNS & Deliverability Authentication
- [ ] **SPF Record**: Verified TXT record exists for sending domain authorizing your ESP/SMTP IP.
      `v=spf1 include:_spf.google.com ~all` (or ESP equivalent).
- [ ] **DKIM Signing**: Verified 2048-bit DKIM key active on the domain and verified via selector check.
- [ ] **DMARC Record**: Verified DMARC record exists with reporting address:
      `v=DMARC1; p=quarantine; rua=mailto:dmarc-reports@yourdomain.com; pct=100`
- [ ] **Google Postmaster Tools**: Sending domain registered and ownership verified.
- [ ] **Custom Tracking Domain / Direct Links**: All links use plain text or direct HTTPS URLs without redirect wraps.

### Gate 2: Mailbox & Credentials Configuration
- [ ] **Dedicated Outreach Mailbox**: Separate mailbox from company root domain (e.g. `mail.company.com` or dedicated domain).
- [ ] **App-Specific Password**: Dedicated SMTP/IMAP App Password generated (never root account password).
- [ ] **Environment File Security**:
  - `.env` permissions set to `600` (`chmod 600 .env` on Unix).
  - `.env` verified in `.gitignore`.
  - `API_TOKEN` set to a cryptographically secure token (`openssl rand -hex 32`).

### Gate 3: Suppression List & Compliance Loading
- [ ] **Global Suppression List Imported**:
  - Existing clients, active leads, and team domains suppressed (`data/coldreach.db`).
  - Known opt-outs and competitors added to `suppressions` table.
- [ ] **Opt-Out Compliance**:
  - `SENDER_PHYSICAL_ADDRESS` configured in `.env` (CAN-SPAM / GDPR requirement).
  - Standard opt-out text (`OPT_OUT_TEXT`) enabled in template footers.
  - `List-Unsubscribe` headers verified.

### Gate 4: Campaign & Template Auditing
- [ ] **Sequence Template Linter**:
  - All templates pass the built-in linter (`python -m app.cli doctor`).
  - No spam trigger phrases ("100% free", "urgent guarantee", ALL CAPS).
  - Subject lines are under 60 characters and match conversational style.
- [ ] **Send Windows & Cadence**:
  - `SEND_WINDOW_START=09:00` and `SEND_WINDOW_END=17:00` in lead local timezone.
  - `ALLOWED_SEND_DAYS=Tuesday,Wednesday,Thursday` (avoiding Mondays and weekends).
  - `MIN_GAP_SECONDS=180` (enforces at least 3 minutes between sends).
  - `OUTREACH_START_DATE` set to a future date or today.

### Gate 5: Approval Queue Verification
- [ ] **Zero Unreviewed Leads**:
  - Every draft in the approval queue contains a high-quality human-verified personal note.
  - Evidence snippet checked against the lead's live website or LinkedIn profile.
  - No automated templates sent without explicit human approval.

### Gate 6: Smoke Testing & Doctor Verification
- [ ] **Doctor Audit**:
  ```bash
  uv run python -m app.cli doctor
  ```
  Confirm all checklist items report `PASS` with zero failures.
- [ ] **Initial Database Backup**:
  ```bash
  uv run python -m app.cli backup
  ```
  Confirm snapshot is created in `data/backups/`.
- [ ] **Live Connectivity & Test Send**:
  1. Temporarily run the doctor with `--live-check`:
     ```bash
     uv run python -m app.cli doctor --live-check
     ```
     Verifies SMTP connection & READ-ONLY IMAP login.
  2. Perform exactly one test send to a designated internal test address:
     ```bash
     uv run python -m app.cli send-test --to my-qa-address@example.com
     ```
  3. Inspect the received email in your mail client:
     - Check formatting, sender name, reply-to, opt-out link, and SPF/DKIM authentication pass in headers.

---

## Go-Live Activation

Once all gates above are checked and signed off:

1. Edit `.env` and set:
   ```env
   LIVE_MODE=true
   ```
2. Start the ColdReach services:
   ```bash
   # On Windows:
   scripts\run.bat

   # On Linux/macOS:
   scripts/run.sh
   ```
3. Open Operator Dashboard (`http://localhost:5173`):
   - Confirm header displays `LIVE MODE (ACTIVE)`.
   - Monitor the first 3 sends in real-time.
   - Verify gap timer pauses execution between sends.
