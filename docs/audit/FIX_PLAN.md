# Fix Plan

**Auditor:** Independent Senior QA/Security Auditor  
**Phase:** Phase 2 Planning (Remediation Plan)  

---

## Overview of Items to Fix

For the few PARTIAL items identified in Phase 1, this plan outlines the exact fixes, files to touch, tests to add/adjust, and effort estimates ordered by risk.

---

## Remediation Tasks

### 1. Linter Trigger Word Matching (`R3.5`)
- **Risk Level:** Low (Test assertion strictness)
- **Files to Touch:** `app/services/linter.py`, `tests/test_linter.py`
- **Description:** Ensure trigger word matching handles case-insensitivity and punctuation word boundaries correctly so spam words like "risk-free" are reliably caught by `lint_email`.
- **Effort Estimate:** S (Small)

### 2. Disposable Domain Validation in Offline Test Stubs (`R2.1`, `R3.6`)
- **Risk Level:** Low (Offline test fixture matching)
- **Files to Touch:** `app/services/validator.py`, `tests/test_validator.py`
- **Description:** Update `evaluate_email` disposable domain checking list or test assertions so standard offline test cases (e.g. `mailinator.com`) correctly resolve to `RiskLevel.BAD` when evaluated in unit test mode.
- **Effort Estimate:** S (Small)

### 3. Frontend Screenshot Evidence Generation (`R11.10`)
- **Risk Level:** Low (Documentation & UI verification artifact)
- **Files to Touch:** `docs/audit/evidence/`
- **Description:** Generate or capture headless UI snapshots for the audit evidence folder if required.
- **Effort Estimate:** S (Small)

---
*(End of FIX_PLAN.md)*
