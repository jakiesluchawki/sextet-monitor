# Mieszko Monitor

Private single-user Phase 1, approved on 2026-08-27. Work incrementally. Never copy World Monitor code. Source code licenses do not grant upstream data rights.

Stack: Python 3.13/FastAPI, PostgreSQL 17/PostGIS 3, Next.js/TypeScript/MapLibre. Four Compose services: web, api, worker, db. Default local-only, AI disabled, Radar disabled without read-only token. Never publish, open LAN ports, create provider accounts, download LLM weights, or call cloud AI without separate authorization.

Approved research: ../outputs/mieszko-monitor-phase0/. Distinguish incidents, advisories, vulnerability notices and measurements. Unknown coordinates/dates stay unknown. Do not fabricate data, confirmations, anomaly scores or causes. Count independent originating evidence, not mirrors/URLs/languages.

Use contracts.py and API_CONTRACT.md. Add normalization/query tests and integration checks. Keep edits in this repo. Secrets in .env only, excluded from git/logs. Sandbox read-only: request escalation for writes, installs and runtime commands; never bypass permissions. Do not reset/delete others' work. Do not commit without being asked.
