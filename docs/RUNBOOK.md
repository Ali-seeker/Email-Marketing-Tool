# ColdReach Operator Runbook

This runbook outlines operational procedures and recovery workflows for ColdReach operators.

---

## 1. When the Kill-Switch Trips

### Trigger Conditions
The automated kill-switch engages and sets system health to `HALTED` whenever:
- Hard bounce rate across the rolling window exceeds **2.0%** (minimum 50 sends).
- 3 hard bounces occur within the last 10 consecutive sends.
- Complaint or abuse report received (spam flag, FBL notification).
- Consecutive send failure rate exceeds **15%**.
- Authentication / policy rejection errors (e.g., SMTP 550 DKIM/SPF/DMARC failure or account suspension).

### Immediate Worker Behavior
When halted, the worker:
1. Logs `CRITICAL` alert with the triggering rule.
2. Emits an in-app and webhook alert to the operator.
3. Immediately stops claiming drafts or calling SMTP send methods.
4. **Continues inbox polling** (so incoming replies and bounce reports are still recorded).

### Resolution Steps
1. **Identify the Cause**:
   - Check the Operator Dashboard banner or run:
     ```bash
     uv run python -m app.cli doctor
     ```
   - Query recent audit events:
     ```bash
     # Check the last 10 events
     sqlite3 data/coldreach.db "SELECT event_type, payload, created_at FROM events ORDER BY id DESC LIMIT 10;"
     ```
2. **Audit Failed Leads**:
   - Inspect the emails that bounced or raised complaints.
   - Verify if an invalid CSV list was imported or lead sources had expired domains.
3. **Add Suppressions**:
   - Immediately add problematic domains or addresses to the suppression list:
     ```bash
     # Via UI or database
     sqlite3 data/coldreach.db "INSERT INTO suppressions (pattern, reason, is_domain) VALUES ('bad-domain.com', 'bounce spike', 1);"
     ```
4. **Safely Resume Sending**:
   - Do **NOT** resume until the root cause is resolved and verified.
   - Once fixed, resume via the Dashboard UI ("Resume Sending" button) or REST API:
     ```bash
     curl -X POST http://127.0.0.1:8000/api/v1/system/resume \
       -H "X-API-Token: YOUR_API_TOKEN" \
       -H "Content-Type: application/json" \
       -d '{"reason": "Investigated bounce spike, removed stale lead list and added 4 suppressions"}'
     ```

---

## 2. When Bounce Rate Rises (Warning State)

### Early Warning Thresholds
- Soft bounces or temporary deferrals > 5%.
- Hard bounce rate between 1.0% and 2.0%.

### Actions
1. **Pause Active Campaigns**:
   - In the Campaigns page, pause campaigns with high bounce rates.
2. **Re-validate Pending Leads**:
   - Run email validation on uncontacted leads:
     ```bash
     # Inspect high-risk leads in queue
     curl -s http://127.0.0.1:8000/api/v1/leads?risk=RISKY -H "X-API-Token: YOUR_API_TOKEN"
     ```
   - In the Leads view, filter by `Status: PENDING` and select "Validate Leads". Remove or reject leads marked `DISPOSABLE`, `NO_MX`, or `ROLE_BASED`.
3. **Verify DNS & Sender Reputation**:
   - Check Google Postmaster Tools for spam rate, domain reputation, and IP reputation.
   - Check Mail-tester or MXToolbox for SPF, DKIM, and DMARC alignment.

---

## 3. When Gmail / Google Workspace Shows a Warning

If Google Workspace or Gmail displays an outreach warning, rate-limit deferral (421 4.7.0), or spam warning:

1. **Stop Sending Immediately**:
   - Click "Emergency Pause" in the dashboard header or trigger kill-switch:
     ```bash
     curl -X POST http://127.0.0.1:8000/api/v1/system/killswitch \
       -H "X-API-Token: YOUR_API_TOKEN" \
       -d '{"reason": "Gmail deliverability warning"}'
     ```
2. **Check Daily Volume & Ramp-Up**:
   - ColdReach enforces a hard cap of 30 emails/day, with ramp-up starting at 5/day. Verify that `DAILY_SEND_LIMIT` has not been overridden too high.
   - Confirm `MIN_GAP_SECONDS` is at least 180 seconds (3 minutes) to maintain natural sending cadence.
3. **Review Personalization & Note Quality**:
   - Ensure outreach messages contain distinct personalized notes.
   - Verify unsubscription links (`List-Unsubscribe` header and body text) are clear and functional.
4. **Rest the Domain**:
   - Keep sending paused for 48 to 72 hours.
   - Continue monitoring inbox to process any delayed replies or unsubscriptions.

---

## 4. How to Restore from Backup

ColdReach provides an online SQLite backup command that generates hot, consistent snapshots.

### Listing Backups
Backups are archived in `data/backups/`:
```bash
ls -l data/backups/
# Output: coldreach_backup_20261012_143000.db
```

### Restoration Procedure
1. **Stop All Processes**:
   - Terminate the backend API and worker daemon:
     ```bash
     # Close the worker and API terminal windows or terminate pids
     ```
2. **Archive the Current Database**:
   ```bash
   mv data/coldreach.db data/coldreach_corrupted_$(date +%s).db
   ```
3. **Copy the Target Snapshot**:
   ```bash
   cp data/backups/coldreach_backup_YYYYMMDD_HHMMSS.db data/coldreach.db
   ```
4. **Verify Database Integrity**:
   ```bash
   sqlite3 data/coldreach.db "PRAGMA integrity_check;"
   # Output should be: ok
   ```
5. **Run Doctor**:
   ```bash
   uv run python -m app.cli doctor
   ```
6. **Restart Services**:
   ```bash
   # On Windows:
   scripts\run.bat
   # On Linux / macOS:
   scripts/run.sh
   ```

---

## 5. Daily Operator Routine

1. **Morning (09:00 local)**:
   - Open Dashboard (`http://localhost:5173`).
   - Check System Health banner (`HEALTHY` vs `HALTED` / `WARNING`).
   - Navigate to **Approval Queue**: inspect generated drafts, verify evidence snippets, approve or edit drafts.
2. **Midday (13:00 local)**:
   - Check **Inbox**: review classified replies (`POSITIVE`, `OBJECTION`, `UNSUBSCRIBE`, `AUTO_REPLY`).
   - Follow up promptly with positive leads.
3. **Evening (18:00 local)**:
   - Run backup:
     ```bash
     uv run python -m app.cli backup
     ```
   - Review daily metrics (sent count vs ramp-up ceiling, bounce count).
