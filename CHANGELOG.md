# Changelog

All notable changes to this project will be documented in this file.

## [3.1.0] - 2026-07-14 — Evidence-based editorial DNA + pipeline robustness

### Added
- **Separator-free message design**: hierarchy from spacing, bold labels, and a frozen semantic
  emoji registry — no ruled lines (grounded in a 476-message market study where zero messages
  used them). Country flag as a deterministic hero-line qualifier; direction arrows on the
  economic verdict line. A registry-lock regression test fails the build if any unregistered
  character ever renders.
- **Validator precision**: number preservation no longer misreads identifiers (`G10`), ordinals
  (`21st`), or month-adjacent day ranges (`July 1-10`) as market figures — six real messages had
  been lost to these false positives; genuine figures remain fully enforced.
- **Arabic-output gate**: reader-facing fields are rejected if not actually Arabic (measured
  threshold; ticker/acronym tolerant) — closes a real published-English-as-translation defect.
- Norway added to the deterministic country vocabulary (recurring real indicator family).

### Fixed
- Telegram edit retries that hit "message is not modified" now count as delivered (they are),
  restoring story-continuity eligibility for such items.
- Persisted error strings are sanitized — request URLs and bot tokens can never reach the
  database again; the one historical occurrence was scrubbed.

## [3.0.0] - 2026-07-13 — Intelligence platform

### Added
- **News Intelligence Engine**: deterministic classification (category, urgency, geography,
  central banks, economic events) with weighted evidence, hard overrides, Decimal-validated
  forecast surprises, and safe fallback — authoritative over AI for operational facts.
- **Premium Arabic message experience**: frozen editorial visual system — semantic icon
  registry, importance-aware length, structured economic-data block (actual → forecast →
  previous + honest verdict line), compact footer, strict no-internal-metadata guarantee.
- **Story Intelligence**: persistent stories and update/correction relationships,
  conservative evidence-based matching (uncertainty never links), published-prior context
  section, restart-safe and idempotent.
- **Editorial Engine**: twelve deterministic editorial modes over one shared DNA — modes set
  badges, the breaking icon, and section order; they never add or remove sections.
- **Indicator Memory**: canonical wording-independent economic-print history
  (country | event | variant | unit-class), honest unkeyed storage, Decimal normalization,
  revision linking, UNIQUE-constraint idempotency, engineering-only quality counters.
- **Macro Context**: deterministic historical facts (forecast streaks, value streaks,
  within-our-records extremes, revisions) handed to the AI as bounded authoritative context —
  minimum-evidence gates (≥3 prints for streaks, ≥6 for extremes) guarantee the platform never
  overclaims its own records; includes a bounded, idempotent, rehearsed backfill tool.
- **Operations**: `scripts/ops_report.py` read-only operational snapshot; CI migration
  round-trip check; sanitized runbooks (`docs/RUNBOOKS.md`); full README overhaul.

### Changed
- AI role narrowed to Arabic prose and clearly-framed interpretation; all layout, numbers,
  comparisons, story identity, and history are application-owned.
- README now documents the systemd production model, the deterministic-vs-AI boundary, and
  honest platform limitations.

### Security
- HTTP-client log suppression (tokens can never reach logs), localhost-only health endpoint,
  secrets exclusively via environment file.

## [2.0.0] - 2026-07-11 — Newsroom foundation (tag: v2.0-newsroom)

### Added
- FinancialJuice RSS ingestion as the permanent source (Discord removed).
- Send-then-edit Telegram delivery: initial English headline in seconds, in-place edit with
  the full Arabic analysis.
- Three-layer duplicate prevention (seeded GUID set, DB unique constraint, content hash).
- Cold-start flood protection; RSS rate-limit backoff.
- GPT-4o-mini Arabic newsroom translation with schema and number-preservation validation.
- Production deployment: systemd service, Alembic migrations at startup, daily backups,
  `/health` endpoint, GitHub Actions CI (format, lint, type, test, Docker build).
- Docker build context hygiene via `.dockerignore`; expanded ignore rules for local secrets,
  caches, logs, databases, and virtual environments.

## [1.0.0] - 2026-07-06

### Added
- Discord-to-Telegram financial news pipeline.
- AI translation and enrichment support.
- Duplicate detection and normalization.
- Telegram alert formatting.
- FastAPI health endpoint.
- Docker and Docker Compose support.
