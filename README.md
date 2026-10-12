# ColdReach 🚀

A self-hosted, safe-by-default cold email outreach automation system built for software startups.

Designed for thoughtful, high-conviction outbound pipeline generation with strict deliverability guards, human-in-the-loop approvals, and rock-solid fail-safes.

---

## Key Features

- **Safe By Default**: `LIVE_MODE=false` out of the box. Zero network sockets or remote mail servers are contacted until explicitly configured in `.env`.
- **Ramp-Up Schedule**: Enforces a strict ramp-up curve (5/day up to a maximum hard ceiling of 30/day) with a minimum 180s gap between sends.
- **Human-in-the-Loop Approval Queue**: Every initial outreach email requires an approved, evidence-backed personalized note before dispatch.
- **Automated Kill-Switch**: Halts sending if the 7-day bounce rate exceeds 2.0%, if 3 bounces occur within 10 sends, or upon spam complaint signals.
- **Smart Inbox Classifier**: Classifies replies (positive, objection, unsubscribe, auto-reply, bounce) and automatically cancels remaining sequence steps.
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
git clone https://github.com/your-org/coldreach.git
cd coldreach

# Install Python dependencies
uv sync --all-extras

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
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2: Background Scheduling Worker
uv run python -m app.worker

# Terminal 3: Operator Dashboard (Vite + React)
cd frontend && npm run dev
```

Dashboard is accessible at: **`http://localhost:5173`**  
API OpenAPI Docs: **`http://127.0.0.1:8000/docs`**

---

## Configuration Reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `LIVE_MODE` | `false` | Dry-run mode (`false`) stores messages locally without opening sockets. `true` activates real SMTP/IMAP. |
| `API_TOKEN` | *(required)* | 32+ char token required in the `X-API-Token` HTTP header for all REST calls. |
| `DATABASE_URL` | `sqlite:///./data/coldreach.db` | Local SQLite database in WAL mode. |
| `OUTREACH_START_DATE` | *(required)* | ISO-8601 date (e.g. `2026-11-01`). No sends planned before this date. |
| `DAILY_SEND_LIMIT` | `30` | Maximum emails per day per mailbox. Hard-capped at 30. |
| `MIN_GAP_SECONDS` | `180` | Minimum delay between consecutive emails. Hard minimum of 120s. |
| `SEND_WINDOW_START` | `09:00` | Start of sending window in lead's local timezone. |
| `SEND_WINDOW_END` | `17:00` | End of sending window in lead's local timezone. |
| `ALLOWED_SEND_DAYS` | `Tuesday,Wednesday,Thursday` | Allowed outreach days (avoids Mondays and weekends). |
| `SMTP_HOST` | `smtp.gmail.com` | Outbound mail server. |
| `SMTP_PORT` | `587` | Outbound STARTTLS port. |
| `SMTP_USER` | *(required in live)* | Outreach mailbox email address. |
| `SMTP_PASSWORD` | *(required in live)* | Dedicated App Password (never root account password). |
| `IMAP_HOST` | `imap.gmail.com` | Inbound mail server. |
| `IMAP_PORT` | `993` | Inbound SSL port. |
| `SENDER_NAME` | *(required)* | Human display name (e.g., `Alex Miller`). |
| `SENDER_PHYSICAL_ADDRESS` | *(required)* | Valid physical mailing address for CAN-SPAM/GDPR compliance. |
| `HUNTER_API_KEY` | *(optional)* | Hunter.io key for enhanced email verification. |
| `OPENAI_API_KEY` | *(optional)* | OpenAI key for research note drafting. |

---

## Daily Operator Workflow

```text
Morning Review (09:00)
       ↓
Check Dashboard Health (HEALTHY vs HALTED)
       ↓
Open Approval Queue: Review & Approve Drafts
       ↓
Worker Plans & Sends on Schedule (Respecting Gap & Send Windows)
       ↓
Review Inbox: Engage Positive Replies
       ↓
Evening Run: Execute Automated Database Backup
```

### 1. Importing Leads
1. Navigate to **Leads** -> **Import CSV**.
2. Select your CSV file containing columns: `email`, `first_name`, `company_name`, `website`, `country`, `timezone`.
3. The import pipeline executes automatically:
   - Sanitizes formula injections (`=cmd` -> `'=cmd`).
   - Deduplicates against existing leads and domain suppresses.
   - Validates email syntax, disposable domains, and MX records.
   - Assigns a risk level: `VALID`, `RISKY`, or `INVALID`.

### 2. Working the Approval Queue
1. Navigate to **Approval Queue**.
2. Inspect pending Step 1 drafts.
3. Review the **personal note** and **evidence snippet** found by the research scraper.
4. If the note is strong, click **Approve**; if weak, edit inline or click **Reject**.
5. Once approved, the lead is scheduled into the planner queue.

### 3. Reading the Dashboard
- **System Health Banner**: Shows `HEALTHY`, `WARNING`, or `HALTED (KILLSWITCH ENGAGED)` with the active trigger reason.
- **KPI Metrics**: Total Sent, Delivery Rate, Reply Rate, and 7-day Bounce Rate with safe threshold indicators.
- **Ramp-Up Visualizer**: Current daily volume versus current tier cap and the 30/day ceiling.
- **Pipeline Funnel**: Leads Discovered -> Enrolled -> Sent -> Replied -> Won.
- **Activity Feed**: Real-time event log of sends, bounce detections, and reply classifications.

---

## CLI Tools

### 1. System Doctor (`doctor`)
Runs a comprehensive 12-point readiness check:
```bash
uv run python -m app.cli doctor
```
When preparing for production, perform an online connectivity test:
```bash
uv run python -m app.cli doctor --live-check
```

### 2. Database Backup (`backup`)
Creates a hot SQLite snapshot into `data/backups/` and automatically prunes snapshots older than 14 runs:
```bash
uv run python -m app.cli backup
```

### 3. Single Test Send (`send-test`)
Sends exactly ONE live email to verify formatting and deliverability (refuses any address registered as a lead):
```bash
uv run python -m app.cli send-test --to my-qa-address@example.com
```

---

## Testing & Quality

Run the comprehensive test suite (Unit, Integration, Chaos, and Simulation):

```bash
# Windows
scripts\test.bat

# Linux / macOS
./scripts/test.sh
```

### Test Categories
- **30-Day Simulation (`tests/e2e/test_simulation.py`)**: Simulates 43,200 virtual minutes with 400 leads across multiple timezones, asserting gap, ramp-up, and cadence invariants on every tick.
- **Chaos & Resilience (`tests/test_chaos.py`)**: Validates process crash recovery, SQLite locking, SMTP backoffs, backward clock jumps, 50,000-row streaming imports, and CRLF injection defenses.
- **Security Audit (`tests/test_security.py`)**: Confirms zero secrets in API responses, non-local interface binding guards, and `LIVE_MODE` immutability via API.

---

## Documentation

- [RULES.md](RULES.md) - Mandatory architectural and deliverability rules.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) - Incident handling, kill-switch recovery, and backup restoration.
- [docs/GO_LIVE_CHECKLIST.md](docs/GO_LIVE_CHECKLIST.md) - Pre-flight verification gates before enabling `LIVE_MODE=true`.
- [docs/API.md](docs/API.md) - REST API specification and endpoint documentation.
