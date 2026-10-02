# Job Engine

A modular, local-first job discovery and application assistant for Python, data engineering, backend, and GenAI/RAG roles.

Job Engine scores jobs against a verified candidate profile, generates evidence-grounded cover-letter drafts, and enforces explicit human approval before any external action. It never scrapes restricted platforms, never invents credentials, and defaults to dry-run mode.

---

## Features

| Area | What it does |
|------|-------------|
| **Ingestion** | Import jobs via CSV upload or manual JSON entry through a source-neutral `JobIngestor` interface |
| **Canonicalization** | Normalize URLs by stripping tracking parameters (`utm_*`, `ref`, `fbclid`, etc.) while preserving functional query parameters |
| **Deduplication** | Detect duplicate jobs by canonical URL or by (company + title + location) matching |
| **Fit Scoring** | Explainable weighted scoring — Role/title 25%, Core skills 30%, Domain 15%, Seniority 10%, Location 10%, Evidence 10% |
| **Skill Analysis** | Matched, missing, uncertain, and negated skill detection (e.g. "no Python required") |
| **Document Generation** | Evidence-grounded cover-letter drafts, resume keyword suggestions, and fit summaries |
| **Approval State Machine** | `NEW → REVIEWED → DRAFTED → APPROVED → APPLIED` with `REJECTED` / `WITHDRAWN` requiring a reason |
| **Audit Logging** | Every state transition and document generation is logged with content hashes |
| **Dry-Run by Default** | All analytical and generation endpoints default to `dry_run=True` — no persistence without explicit opt-in |
| **Local API** | FastAPI service bound to `127.0.0.1` only |

---

## Project Structure

```
job-engine/
├── src/
│   ├── api/
│   │   └── main.py              # FastAPI endpoints (8 routes)
│   ├── models/
│   │   ├── base.py              # SQLAlchemy Base & JSON list type
│   │   ├── job.py               # JobModel, JobCreate, JobRead, Draft
│   │   ├── profile.py           # CandidateProfile, Evidence
│   │   └── audit.py             # AuditLog model
│   ├── tracking/
│   │   ├── database.py          # Engine, session, init_db
│   │   └── crud.py              # create_job, get_job
│   ├── approval.py              # State machine & transition rules
│   ├── canonicalize.py          # URL normalization
│   ├── config.py                # Pydantic settings
│   ├── deduplicate.py           # Duplicate detection
│   ├── document_generation.py   # Cover letter, fit summary, keyword suggestions
│   ├── ingestors.py             # CSV & URL ingestors
│   └── scoring.py               # Weighted fit scoring
├── tests/
│   ├── conftest.py              # Shared fixtures (in-memory SQLite sessions)
│   ├── test_models.py           # Pydantic & SQLAlchemy model tests
│   ├── test_crud.py             # Database CRUD tests
│   ├── test_milestone2.py       # Ingestion, canonicalization, dedup, scoring
│   ├── test_milestone3.py       # State machine, document generation, audit
│   ├── test_milestone3_final.py # Atomic transactions, draft integrity, dry-run
│   ├── test_api.py              # Endpoint behavior & contract tests
│   ├── test_api_security.py     # Security validation (leakage, dry-run, reasons)
│   └── test_run.py              # Localhost binding verification
├── run.py                       # Uvicorn launcher (127.0.0.1:8000)
├── pyproject.toml               # Dependencies & project metadata
└── .gitignore
```

---

## Quick Start

```bash
# Clone
git clone https://github.com/ManoharVit/job-engine.git
cd job-engine

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e ".[dev]"
pip install uvicorn httpx2

# Run tests
pytest -q --cov=src --cov-report=term-missing

# Start the local API server
python run.py
# → http://127.0.0.1:8000/docs
```

---

## API Endpoints

| Method | Path | Purpose | Dry-run |
|--------|------|---------|---------|
| `POST` | `/api/jobs` | Import a single job (JSON) | N/A |
| `POST` | `/api/jobs/csv` | Bulk import from CSV (`text/csv` body) | N/A |
| `GET` | `/api/jobs` | List jobs, optional `?company=` filter | N/A |
| `GET` | `/api/jobs/{id}` | Get a specific job | N/A |
| `POST` | `/api/jobs/{id}/review` | Explainable fit score & skill analysis | Always (read-only) |
| `POST` | `/api/jobs/{id}/draft` | Generate cover-letter draft | Default `true` |
| `POST` | `/api/jobs/{id}/state` | Transition approval state | Configurable |
| `GET` | `/api/jobs/{id}/export` | Export approved application package | N/A (gated by APPROVED) |

---

## State Machine

```
NEW ──→ REVIEWED ──→ DRAFTED ──→ APPROVED ──→ APPLIED
 │         │           │           │            │
 └─────────┴───────────┴───────────┴────────────┘
                       ↓
              REJECTED / WITHDRAWN
              (reason required)
```

- **REJECTED** and **WITHDRAWN** are terminal states reachable from any active state.
- A transition to either terminal state requires a non-empty `reason` string.
- `APPLIED` requires a persisted `Draft` record to exist.

---

## Scoring Weights

| Component | Weight | Description |
|-----------|--------|-------------|
| Role/title match | 25% | Target role keyword overlap |
| Core technical skills | 30% | Must-have skill coverage with word-boundary matching |
| Domain match | 15% | Industry/domain alignment |
| Seniority match | 10% | Experience level fit |
| Location/remote match | 10% | Geographic and remote-work preference |
| Evidence strength | 10% | Proportion of claims backed by verified evidence |

Missing must-have skills apply a penalty. Negated mentions (e.g. "no Python required") are detected and excluded from matching.

---

## Safety & Security Constraints

- **Localhost only** — the API binds to `127.0.0.1` by default
- **Dry-run by default** — generation and review endpoints never persist unless explicitly opted in
- **No scraping** — does not access LinkedIn, Naukri, or any restricted platform
- **No browser automation** — no Playwright, no session cookies, no CAPTCHA bypass
- **No fabrication** — unsupported claims are marked `[VERIFY: skill experience]`
- **Sanitized errors** — rejected CSV rows return only safe identifiers (title, source_job_id), never raw SQL, tracebacks, or environment variables
- **Human approval required** — export is gated by the `APPROVED` state
- **Audit trail** — every state transition and document generation is logged with content hashes (not raw content)

---

## Testing

```bash
# Run the full suite with coverage
./venv/bin/pytest -q --cov=src --cov-report=term-missing

# Current status: 76 tests, 96% coverage, 0 warnings
```

Key test categories:
- **Model validation** — Pydantic schema enforcement, missing fields, type coercion
- **Ingestion** — valid CSV, malformed rows, row-number tracking, URL canonicalization
- **Deduplication** — canonical URL matching, company+title+location matching
- **Scoring** — empty profiles, negated skills, missing evidence, boundary cases
- **State machine** — invalid transitions, reason enforcement, draft prerequisite for APPLIED
- **Document generation** — atomic transactions, duplicate detection, dry-run isolation
- **API security** — no secret leakage, dry-run persistence checks, localhost binding

---

## Milestones

| Tag | Milestone | Summary |
|-----|-----------|---------|
| — | **M1** | Foundation — Pydantic models, SQLAlchemy ORM, database initialization |
| — | **M2** | Ingestion, canonicalization, deduplication, explainable scoring |
| — | **M3** | Approval state machine, document generation, audit logging, atomic transactions |
| `milestone-4-local-api` | **M4** | FastAPI local review API with 8 endpoints |
| — | **M4.1** | Maintenance — lifespan upgrade, deprecation fixes, dependency pinning |

---

## Known Limitations

- **SQLite only** — PostgreSQL support is declared but unverified; migrations have not been tested
- **No external fetching** — RSS, career-page, and email ingestors are not yet implemented
- **No authentication** — the API has no auth layer (mitigated by localhost binding)
- **No frontend** — interaction is via API calls or test client only
- **Single-process** — no Celery/Redis async processing yet

---

## License

This project is for personal use. No license has been declared yet.