# ColdReach 🚀

A self-hosted, safe-by-default cold email outreach automation system built for software startups.

## Overview

ColdReach automates outbound pipeline creation:
- **Lead Intake**: CSV parsing, deduplication, and syntax/MX validation.
- **Personalization**: Plain-text Jinja2 email templates with customizable merge tags.
- **Slow & Safe Delivery**: Strict scheduling, rate limits (max 30 emails/day per mailbox, minimum 120s between emails).
- **Reply & Bounce Detection**: IMAP monitoring for auto-stopping follow-ups when replies are detected.
- **Dry-Run by Default**: Unless `LIVE_MODE=true` is set explicitly, emails are only stored locally and never touch remote mail exchangers.

---

## Non-Negotiable Rules

See [RULES.md](RULES.md) for core architectural and safety principles.
1. **SAFE BY DEFAULT**: `LIVE_MODE` defaults to `false`. Real SMTP emails are never sent unless explicitly enabled in `.env`.
2. **Zero Hardcoded Secrets**: Secrets live strictly in `.env`.
3. **Injectable Clock**: All scheduling and cooldown logic uses `app.core.clock.Clock`.
4. **Interface Isolation**: External systems (SMTP, IMAP, LLM, scraper) are abstract interfaces with mock implementations for unit testing.
5. **Plain-Text Standard**: Strictly `text/plain` emails with no tracking pixels or shortened URLs.

---

## Project Structure

```text
ColdReach/
├── app/
│   ├── core/           # Config, SQLite WAL db, logging, clock abstraction
│   ├── models/         # SQLAlchemy ORM models
│   ├── services/       # Business logic (validation, sender, template engine, imap listener)
│   ├── api/            # FastAPI REST endpoints
│   ├── main.py         # FastAPI application entrypoint
│   └── worker.py       # Background scheduler loop (python -m app.worker)
├── tests/              # Pytest test suite with fake clocks and mocks
├── frontend/           # (Upcoming) React + Vite + TypeScript dashboard
├── data/               # Local SQLite database directory (gitignored)
├── .env.example        # Template configuration with safe placeholders
├── RULES.md            # Mandatory engineering rules
├── pyproject.toml      # Dependency & tool configurations
└── README.md           # Documentation
```

---

## Setup & Running

### 1. Prerequisites
- Python 3.11+
- `uv` (recommended) or standard Python `venv`

### 2. Environment Setup

Using `uv`:
```bash
# Sync dependencies and create virtual environment
uv sync --all-extras

# Copy environment template
cp .env.example .env
```

Using standard `python -m venv`:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env
```

### 3. Run the API Server

```bash
uv run uvicorn app.main:app --reload --port 8000
```
Open [http://localhost:8000/api/health](http://localhost:8000/api/health) to inspect health and safety status.

### 4. Run the Background Worker

```bash
uv run python -m app.worker
```

### 5. Running Tests & Linting

```bash
# Run tests
uv run pytest

# Run linter
uv run ruff check .
```
