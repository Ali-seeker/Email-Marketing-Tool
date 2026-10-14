# ColdReach 🚀

A self-hosted, safe-by-default cold email outreach automation system built for software startups.

Designed for thoughtful, high-conviction outbound pipeline generation with strict deliverability guards, human-in-the-loop approvals, and rock-solid fail-safes.

---

## Key Features

- **Safe By Default**: `LIVE_MODE=false` out of the box. Zero network sockets or remote mail servers are contacted until explicitly toggled.
- **Interactive Live Mode Toggle**: Easily toggle between `DRY RUN (SAFE)` and `LIVE MODE (ACTIVE)` directly from the dashboard navbar with 1-click safety confirmation. No server restart needed.
- **Multi-Mailbox Rotation**: Connect multiple sending mailboxes. The system automatically round-robins initial outreach across active accounts, while strictly locking follow-ups to the original contact mailbox for natural conversation continuity.
- **Multi-Inbox Reply Hub & Notifications**: Inbound replies are auto-captured via IMAP, classified by AI sentiment, and routed to individual mailbox tabs. Real-time topbar notifications alert operators to high-intent replies.
- **Ramp-Up Schedule**: Enforces a strict ramp-up curve (5/day up to a maximum hard ceiling of 30/day) with a minimum 120s-180s gap between sends.
- **Human-in-the-Loop Approval Queue**: Every initial outreach email requires an approved, evidence-backed personalized note before dispatch.
- **Automated Kill-Switch**: Halts sending if the 7-day bounce rate exceeds 2.0% (with small-sample size protection), if 3 bounces occur within 10 sends, or upon spam complaint signals.
- **Formula & Injection Neutralization**: Neutralizes spreadsheet formula injection (`=`, `@`, `+`, `-`) and scrubs CRLF header injection from lead data.
- **Self-Diagnostic Doctor**: Built-in CLI command validates system readiness, configuration sanity, and database health.

---

## Quick Start

### 1. Prerequisites
- **Python 3.11+** (managed via `uv` or `venv`)
- **Node.js 18+** & `npm` (for the React operator dashboard)

### 2. Backend & Frontend Installation

```bash
# Clone the repository
git clone https://github.com/Ali-seeker/Email-Marketing-Tool.git
cd Email-Marketing-Tool

# Setup virtual environment and dependencies
python -m venv .venv
.venv\Scripts\activate   # Windows (.venv/bin/activate on Linux/macOS)
pip install -r requirements.txt

# Copy environment configuration
cp .env.example .env

# Install Frontend dependencies
cd frontend
npm install
cd ..
```

Generate a secure random API token and place it in `.env`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Configure `frontend/.env`:
```env
VITE_API_URL=http://127.0.0.1:8000
VITE_API_TOKEN=your_generated_token_from_env
```

### 3. Run the System

#### Single Command (API + Worker + Frontend)
- **Windows**:
  ```cmd
  scripts\run.bat
  ```
- **Linux / macOS**:
  ```bash
  chmod +x scripts/run.sh
  ./scripts/run.sh
  ```

#### Manual Services
```bash
# Terminal 1: REST API (FastAPI)
.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000

# Terminal 2: Background Scheduling Worker
.venv\Scripts\python.exe -m app.worker

# Terminal 3: Operator Dashboard (Vite + React)
cd frontend && npm run dev
```

Dashboard is accessible at: **`http://localhost:5173`**  
API OpenAPI Docs: **`http://127.0.0.1:8000/docs`**

---

## Configuration Reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `LIVE_MODE` | `false` | Dry-run mode (`false`) stores messages locally without opening sockets. `true` activates real SMTP/IMAP (can also be toggled from the dashboard topbar). |
| `API_TOKEN` | *(required)* | 32+ char token required in the `X-API-Token` HTTP header for all REST calls. |
| `DATABASE_URL` | `sqlite:///./data/coldreach.db` | Local SQLite database in WAL mode. |
| `DAILY_SEND_LIMIT` | `30` | Maximum emails per day per mailbox. Hard-capped at 30. |
| `MIN_GAP_SECONDS` | `120` | Minimum delay between consecutive emails. |
| `BOUNCE_RATE_STOP_THRESHOLD_PERCENT` | `2.0` | Maximum allowable 7-day bounce rate before engaging killswitch. |
| `SMTP_HOST` | `smtp.gmail.com` | Outbound mail server. |
| `SMTP_PORT` | `465` (SSL) / `587` (TLS) | Outbound SMTP port. |
| `SMTP_USERNAME` | *(required in live)* | Outreach mailbox email address. |
| `SMTP_PASSWORD` | *(required in live)* | Dedicated Google/Gmail App Password (16 characters). |
| `IMAP_HOST` | `imap.gmail.com` | Inbound mail server. |
| `IMAP_PORT` | `993` | Inbound SSL port. |
| `IMAP_USER` | *(required in live)* | Inbound mailbox username for reply & bounce monitoring. |

---

## Daily Operator Workflow

```text
Morning Review (09:00)
       ↓
Check Dashboard Health & Navbar Mode (Dry-Run vs Live)
       ↓
Open Approval Queue: Review & Approve Drafts
       ↓
Worker Plans & Sends on Schedule (Multi-Mailbox Rotation & Cadence Invariants)
       ↓
Inbound Replies Alert via Topbar Bell -> Switch Inbox & Review
       ↓
Evening Run: Execute Automated Database Backup
```

### 1. Multi-Mailbox Setup
1. Navigate to **Mailboxes** (`/mailboxes`).
2. Add your sending accounts with their dedicated SMTP and IMAP credentials.
3. System will rotate outbound Step 1 messages across all active mailboxes to maintain domain reputation and warm-up pacing.
4. Follow-up sequences automatically stick to the specific mailbox that initiated contact.

### 2. Live Mode vs Dry-Run Control
- Topbar displays an interactive mode badge:
  - **`○ DRY RUN (SAFE)`**: Emails are safely logged locally to DB without contacting external mail servers.
  - **`● LIVE MODE (ACTIVE)`**: Real email delivery via SMTP.
- Click the badge to toggle modes on-the-fly; an alert modal confirms your intent before going live.

### 3. Reviewing Replies & Multi-Inbox Hub
- When prospects reply, sequence follow-ups for that lead are automatically halted.
- The **Bell icon** in the top navigation bar displays a badge with recent positive or high-intent responses.
- In **Replies**, use the account switcher tabs (`All Inboxes` or specific mailboxes) to filter and view conversations.
- Use **Mark Won** to record converted prospects directly into pipeline analytics.

---

## Inspecting the SQLite Database

All system data (leads, campaigns, mailboxes, messages, settings, replies) is stored in `data/coldreach.db`.

### Method 1: VS Code Extension (Recommended)
1. Open the Extensions tab in VS Code (`Ctrl + Shift + X`).
2. Search and install **"SQLite Viewer"** (by Florian Klampfer).
3. In File Explorer, click directly on `data/coldreach.db` to view and filter tables like Excel spreadsheets.

### Method 2: DB Browser for SQLite (GUI)
1. Download the free GUI from [sqlitebrowser.org](https://sqlitebrowser.org/).
2. Open `data/coldreach.db` to browse records, view schemas, and run custom SQL queries.

---

## CLI Tools

### 1. System Doctor (`doctor`)
Runs a comprehensive 12-point readiness check:
```bash
.venv\Scripts\python.exe -m app.cli doctor
```
When preparing for production, perform an online connectivity test:
```bash
.venv\Scripts\python.exe -m app.cli doctor --live-check
```

### 2. Database Backup (`backup`)
Creates a hot SQLite snapshot into `data/backups/` and automatically prunes snapshots older than 14 runs:
```bash
.venv\Scripts\python.exe -m app.cli backup
```

### 3. Single Test Send (`send-test`)
Sends exactly ONE live email to verify formatting and deliverability:
```bash
.venv\Scripts\python.exe -m app.cli send-test --to my-qa-address@example.com
```

---

## Testing & Quality

Run the test suite:

```bash
# Windows
scripts\test.bat

# Or direct pytest
pytest
```

---

## Documentation

- [RULES.md](RULES.md) - Mandatory architectural and deliverability rules.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) - Incident handling, kill-switch recovery, and backup restoration.
- [docs/GO_LIVE_CHECKLIST.md](docs/GO_LIVE_CHECKLIST.md) - Pre-flight verification gates before enabling `LIVE_MODE=true`.
- [docs/API.md](docs/API.md) - REST API specification and endpoint documentation.
