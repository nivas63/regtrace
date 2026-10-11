# RegTrace — Architecture & Solution Document

**ET AI Hackathon (Agentic Edition) · Problem 1: Banking / Financial Regulations**
*AI-assisted compliance assessment. Not legal advice. All bank data and all regulations in the demo are SYNTHETIC and attributed to fictional authorities.*

---

## 1. Executive summary

RegTrace is an agentic compliance-intelligence platform that keeps the chain
**regulation → obligation → applicability → control → evidence → test → gap → risk → remediation → monitoring**
as one versioned, traceable graph. When a regulatory document is uploaded, a supervisor agent plans and runs the analysis
autonomously: it links the document to its regulation and version, diffs it against the previous version, extracts only the changed
obligations, re-maps impacted controls, re-assesses evidence, recalculates gaps and risk, proposes remediation, and produces an impact
report — with every step, tool call and model call persisted for audit.

Design stance: **LLMs turn language into verifiable, structured facts; code computes every number; humans decide interpretations.**
Every obligation, evidence verdict and mapping carries a verbatim quote that is machine-verified against the source.
The system runs at **US$0** (free Gemini / Groq tiers, local Ollama option, local embedding & reranking models).

**Self-assessed position: F3 / D2** (§17–18). D3 is not claimed.

## 2. Problem statement

Banks must keep an evolving, traceable relationship between regulations and their internal controls: which obligations apply, which
controls satisfy them, whether evidence proves those controls work, where the gaps are, how risky they are, what to do, and what a
regulatory change breaks. Inputs are regulatory documents and internal control / policy documents.

## 3. Solution overview

| Capability | How RegTrace does it |
|---|---|
| Ingestion | Magic-byte validation, active-content / zip-bomb checks, content-addressed storage, PyMuPDF/DOCX/XLSX/HTML parsing, OCR fallback, numbering-aware structure recovery, header/footer stripping, clause-aligned chunking, local embeddings, injection scanning |
| Obligation extraction | Batched structured LLM extraction → verbatim-quote gate → independent verifier model → composite confidence → review queue |
| Temporal intelligence | Versions with effective windows; `as_of` queries; section alignment + editorial pre-check + LLM change classification; obligation lineage keys; amendment-circular applier |
| Control understanding | Deterministic control-matrix import + LLM control extraction from policies, reconciled by control reference |
| Mapping | Vector + lexical + rule prior → LLM adjudication of top candidates → quote verification → blended confidence → human review |
| Effectiveness / evidence | Deterministic D·O·E·R score; LLM judges evidence criteria with verified quotes; "never compliant without evidence" |
| Gaps / risk | 8-type rule-based gap engine with monitoring (auto-resolve); deterministic risk formula with factor vectors |
| Remediation | LLM plan; code sets due dates, priority, approval requirement; separate execution tracking |
| Impact analysis | Supervisor-planned workflow triggered by upload; graph traversal report + notification |
| What-if | LLM parses scenario; same deterministic engines run on an in-memory copy |
| Cross-regulation / conflicts | Similarity + same-type-same-period clustering; deterministic conflict signals → LLM explanation → mandatory human review |

## 4. Architecture diagram

```
 Browser (Next.js, 18 pages) ──HTTPS/JWT──▶ FastAPI  (problem+json errors, RBAC, request-id, CORS)
                                              │
            ┌─────────────────────────────────┼──────────────────────────────────┐
            ▼                                 ▼                                  ▼
     Query / RAG (/query)          Workflow engine (durable, Postgres)      Ingestion service
     hybrid retrieval + grounding  queue = workflow_runs (SKIP LOCKED)      validate → store → parse
            │                      in-process worker (or separate)                │
            │                                 ▼                                  │
            │        ┌──────────── Supervisor planner (LLM, constrained) ────────┘
            │        ▼
            │   LLM agents: document-intelligence · obligation-extractor + verifier · applicability ·
            │               control-mapper · evidence-assessor · change-analyst · amendment-parser ·
            │               cross-regulation/conflict · remediation-planner · simulation
            │        │ uses (allow-listed, logged)
            │        ▼
            │   Tool registry ── deterministic engines: effectiveness · gap · risk · graph traversal · search
            │        │
            ▼        ▼
     LLM service: redaction → cache → budget → router (Gemini → Groq → Ollama; quota limiter,
                  retries, circuit breaker) → schema validation + repair → call log
                                              │
 PostgreSQL 16: relational core · pgvector HNSW · full-text · kg_edges graph · RLS · hash-chained audit
 Local volume: content-addressed files · local ONNX models (bge-base-en-v1.5, bge-reranker-base)
```

## 5. Agent architecture

Fourteen candidate agents were reduced to the ones that need autonomous language reasoning; everything numeric is a deterministic engine.

| Agent | Kind | Model tier | Autonomy | Human gate |
|---|---|---|---|---|
| Supervisor (impact planner) | LLM, constrained to a step catalogue; code enforces order, preconditions, required steps; deterministic fallback | fast | autonomous | — |
| Document intelligence | rules first (references, supersession, titles), LLM only when ambiguous | fast | autonomous | ambiguous links |
| Obligation extractor | LLM structured extraction, batched per chapter | reasoning | autonomous | confidence < 0.70 or ambiguity |
| Obligation verifier | independent LLM entailment check | fast | autonomous | — |
| Applicability | rules vs bank profile; LLM for undecidable cases | fast | autonomous (rules) | every LLM decision / UNCERTAIN |
| Control understanding | LLM control extraction from policies + deterministic reconciliation | reasoning | autonomous | mismatches, policy-only controls |
| Control mapper | retrieval + rules prior, LLM adjudication, quote-verified | reasoning | proposes | confidence < 0.75 |
| Evidence assessor | LLM per criterion, quote-verified; no artefact → no call | reasoning | autonomous | — (cannot conclude "compliant") |
| Change analyst | deterministic alignment/diff + LLM classification; lineage | reasoning | autonomous | — |
| Amendment parser | LLM → edit instructions; code applies them | reasoning | proposes | always (consolidated text) |
| Cross-regulation / conflict | deterministic candidates & signals + LLM explanation | fast / reasoning | detects | every potential conflict |
| Remediation planner | LLM plan; code decides dates / approval | reasoning | proposes | P1/P2, production-touching |
| Simulation | LLM parse; deterministic propagation | fast | autonomous | — |

**Communication & state.** Agents never talk to each other; they read and write typed rows (blackboard). `workflow_runs.state` holds IDs
and counts only. **Failure recovery:** retryable errors (provider exhaustion, transient DB/network) are retried with backoff; then an
optional fallback strategy; then the run fails with a `RUN_FAILURE` review — nothing is silently skipped. A crashed process resumes at
the last completed step. **Loop prevention:** plans only contain catalogue steps, no duplicate steps, ≤ 2 replans, ≤ 60 step executions,
per-run LLM call budget enforced atomically in SQL. **Hallucination control:** schema-validated outputs with one repair round, verbatim
quote gate, citation closure, independent verifier, composite confidence, abstention ("Insufficient evidence"), deterministic scoring.

## 6. Data architecture

35 tables (Alembic migrations `0001–0003`). Key groups: tenancy (`banks`, `users`); documents (`documents`, `sections`, `chunks` with
`vector(768)` + generated `tsvector`); regulatory (`regulations`, `regulation_versions`, `obligations`, `obligation_lineage`,
`regulatory_changes`, `obligation_applicability`, clusters, conflicts); controls (`controls`, `control_obligations`, `evidence`,
`control_tests`, `evidence_assessments`, `control_assessments`); findings (`compliance_findings`, `risk_assessments`,
`remediation_actions`, `impact_assessments`, `simulations`); graph (`kg_edges`, time-valid, `NULLS NOT DISTINCT` uniqueness); runs
(`workflow_runs`, `workflow_steps`, `tool_calls`, `llm_calls`, `llm_cache`); governance (`human_reviews`, `audit_log`, `eval_runs`).

**Versioning.** Stable `obligation_key` / `control_key` identities with per-version rows; versions carry `effective_from/to`; chunks copy
the window for cheap temporal filtering; superseded obligations and their findings are retained (`SUPERSEDED`), never deleted.
**Foreign keys** are `RESTRICT` along the lineage chain. **Indexes:** HNSW (cosine) on chunk / obligation / control embeddings, GIN on
`tsv`, validity-window B-tree, graph src/dst indexes, queue index on `(status, available_at)`.

## 7. RAG architecture

query → understanding (type, rewrite, ≤2 sub-queries, as-of date; fast model) → per-query hybrid retrieval with SQL-level filters
(tenant, validity window / current, doc class, quarantine) → dense (pgvector, top 40) + lexical (Postgres FTS OR-semantics, top 40) +
graph expansion (REFERENCES neighbours of fused top 10) → weighted Reciprocal Rank Fusion (k = 60; 1.0 / 0.8 / 0.5) → cross-encoder rerank
of top 30 → threshold (0.2 on sigmoid rerank score) or abstain → compliance facts for status questions → answer with per-claim citations →
citation closure + lexical support check (claims below 0.15 support are dropped) → "Insufficient evidence" if nothing grounded survives.

Chunks are clause-aligned (≤ 600 tokens, 450-token windows with 64-token overlap for long clauses) with a contextual header
(document › chapter › paragraph) embedded alongside. Retrieved text is always spotlighted as `<document>` data. Lexical retrieval is
Postgres FTS, **not true BM25** (ADR-005).

## 8. Knowledge graph

Nodes are domain rows; `kg_edges` holds typed, time-valid relationships: `HAS_VERSION`, `SUPERSEDES`, `AMENDS`, `HAS_SECTION`,
`REFERENCES`, `HAS_OBLIGATION`, `EVOLVED_TO`, `SATISFIED_BY`, `EQUIVALENT_TO`, `POTENTIAL_CONFLICT`, `VIOLATES`, `AFFECTS`,
`MITIGATED_BY`. Traversal is a cycle-safe recursive CTE. The graph answers the questions vectors cannot — "which controls and evidence
does a changed paragraph reach?" — and feeds retrieval (neighbour expansion) and the impact report.

## 9. Database ER (core lineage)

```
regulations 1─* regulation_versions 1─* sections 1─* chunks
                                   └─* obligations *─* controls   (control_obligations: type, coverage, confidence, review)
obligations 1─* obligation_lineage *─1 obligations (prev → next)
controls 1─* control_tests · controls 1─* evidence · controls 1─* control_assessments (per obligation, is_current)
obligations 1─* compliance_findings 1─* risk_assessments · compliance_findings 1─* remediation_actions
workflow_runs 1─* workflow_steps 1─* tool_calls / llm_calls ;  human_reviews → any subject ; audit_log (hash chain per bank)
```

## 10. API architecture

FastAPI under `/api/v1`, OpenAPI at `/api/v1/docs`. JWT bearer auth; permissions per role (admin, analyst, reviewer, control_owner,
auditor). Long-running work returns **202 + run id**. Errors are RFC 7807 `application/problem+json` with stable codes and a request id.
Uploads are idempotent by content hash (plus `Idempotency-Key` header accepted); runs by idempotency key.

Endpoints: `auth/login|me` · `documents/upload|list|{id}|{id}/file` · `regulations|{id}?as_of|{id}/changes|{id}/analyze` ·
`obligations|{id}` · `controls|{id}` · `mappings` · `evidence` · `compliance/assess|findings|findings/{id}` · `risk/heatmap` ·
`remediation|{id} (PATCH)` · `impact|{id}` · `simulation/run|list|{id}` · `contradictions/detect` · `conflicts` · `clusters` · `query` ·
`agent-runs|{id}` · `reviews|{id}/decision` · `audit|audit/verify` · `graph` · `observability/llm` · `eval/run|runs` · `dashboard/summary` ·
`health/live|ready|llm`.

## 11. Security architecture

| Threat | Control |
|---|---|
| Cross-tenant access | Postgres **row-level security** on 18 tables keyed on a transaction-local `app.bank_id`; the app uses a NOSUPERUSER / NOBYPASSRLS role; integration-tested (reads and smuggled writes both blocked) |
| Indirect prompt injection | Retrieved text wrapped as `<document>` data with break-out neutralisation; standing data-only rule in every system prompt; ingestion-time injection scanner quarantines high-risk chunks; agents have no side-effect tools; outputs schema-bound |
| PII egress to model providers | Reversible redaction (PAN, Aadhaar, account, IFSC, phone, email) on bank content before egress, restored in validated output |
| Malicious files | Magic-byte typing, size / page limits, encrypted-PDF rejection, PDF JavaScript / launch / embedded-file rejection, zip-bomb and macro checks |
| Tampering with history | Append-only audit log (app role has no UPDATE/DELETE), per-bank SHA-256 hash chain, `/audit/verify` pinpoints the first broken entry |
| Privilege escalation | RBAC at the API boundary; segregation of duties (the proposer or triggering user cannot approve remediation) |
| Secrets | `.env` for the prototype (never committed); SecretStr in config; production: Vault/KMS |

Passwords: scrypt. Encryption at rest is a **production item** (disk / TDE). Free-tier providers may use inputs to improve products —
only synthetic bank data is sent; for real data, route to local Ollama or an enterprise tier (config only).

## 12. Evaluation architecture

`app/evaluation/harness.py` measures from the live database (what the agents actually produced) against `data/golden/*.jsonl`
(65 obligations, 35 mappings, 11 seeded gaps, 5 changes, 2 conflicts, 2 clusters, 25 retrieval queries). Suites: parsing, extraction
(P/R/F1, type / modality / period accuracy, unverified-output rate, calibration ECE), mapping, gaps, retrieval (Recall@1/3/5, MRR),
change detection, cross-regulation, operations (success rate, cache hits, latency, equivalent cost). Results are stored in `eval_runs`
and shown on the Reliability page.

## 13. Observability architecture

structlog JSON with request-id / run context; every workflow step (attempt, status, decisions, output, duration), tool call (args,
status, latency) and LLM call (purpose, prompt version, tier, provider, model, tokens, latency, cache hit, repairs, provider routing trail,
equivalent cost) is persisted and visible in the Agent Run trace and the Reliability page. `/health/llm` exposes providers, circuit state
and quota usage without spending quota.

## 14. Failure recovery architecture

| Failure | Strategy |
|---|---|
| LLM 429 / quota | per-model fallback chain (quotas are per model: 4 Gemini + 2 Groq reasoning models); short retry-after waited out on the same model, long ones block it and fall through; if every model is throttled, the router queues for the soonest window (≤ 90 s) instead of failing |
| Model returns the wrong id | items are given short aliases (O1, F1, E1…) and answers are resolved by alias, original id or unambiguous paragraph number — never guessed |
| Retried step | multi-transaction steps (extraction, evidence assessment, diff) first remove rows written by earlier attempts of the same run, so retries are idempotent; failed runs can be resumed from the last completed step (`POST /agent-runs/{id}/retry`) |
| LLM 5xx / timeout / network | exponential backoff with jitter per provider, then next provider; circuit breaker opens after 3 failures |
| Invalid JSON / schema | one repair round with validator errors; then `StructuredOutputError` → step fails → retry / review |
| All providers down | step retried 3×; run fails with a RUN_FAILURE review; planner falls back to deterministic templates |
| Low retrieval score | abstain with "Insufficient evidence" |
| Missing evidence | no model call; criteria INSUFFICIENT; rating INSUFFICIENT_EVIDENCE |
| Duplicate document | content-hash dedupe (no reprocessing); duplicate regulation versions detected by reference number |
| DB outage | readiness 503; step retry; runs resume from last completed step |
| Crash mid-run | orphaned RUNNING runs re-queued at worker start; completed steps skipped |
| Agent loop | plan validation, replan cap, step-execution cap, LLM budget |
| OCR failure | page flagged in the parse report (prototype) |

## 15. Deployment architecture

`docker compose up`: `postgres` (pgvector/pg16), `api` (FastAPI + in-process worker; entrypoint runs migrations and warms models), `web`
(Next.js standalone). Volumes: `pgdata`, `uploads`, `models`; `./data` mounted for the corpus. Scale-out path: set
`WORKER_IN_PROCESS=false` and run `python -m app.workers.worker` replicas (queue is `SKIP LOCKED`); move quota counters to Postgres/Redis.

## 16. Feature-to-requirement mapping

| # | Official feature | Implemented in | Demo evidence |
|---|---|---|---|
| 1 | Regulatory ingestion | `ingestion/*`, `services/ingestion.py` | Documents page; parse report; 108/108 paragraphs recovered |
| 2 | Change intelligence | `agents/change.py` | Upload KYC v2 → 4 material changes, 1 editorial suppressed, word diffs |
| 3 | Obligation extraction | `agents/regulatory.py` | Obligation explorer with highlighted verified quotes |
| 4 | Control-framework understanding | control-matrix import + `extract_policy_controls` | Controls page; policy reconciliation reviews |
| 5 | Regulation-to-control mapping | `agents/controls.py` | Mapping matrix with reasons, verified quotes, review status |
| 6 | Control effectiveness | `domain/scoring.py` | Control page factor breakdown (design / operating / evidence / recency) |
| 7 | Evidence-based assessment | `assess_evidence` | Criterion verdicts with verified evidence quotes; C-IT-01 = insufficient evidence |
| 8 | Gap identification | `identify_gaps`, `reconcile_gaps` | Findings page (8 gap types, provisional flags, auto-resolve) |
| 9 | Risk prioritisation | `score_risk` | Heatmap; per-finding factor vector and reason |
| 10 | Remediation | `agents/analysis.py` | Remediation tracker with approval gate |
| 11 | Autonomous impact analysis | supervisor + `process_document` | Agent trace of the v2 upload; impact report |
| 12 | What-if simulation | `agents/simulation.py` | Simulation page (before/after ratings, workload) |
| 13 | Cross-regulation intelligence | `analyse_cross_regulation` | Equivalent-obligation clusters, shared-control suggestion |
| 14 | Contradiction detection | conflict signals + LLM + review | Potential conflicts: retention vs erasure; log retention 180 vs 90 days |

## 17. D1/D2/D3 assessment

**Claim: D2.** Inputs are mostly structured/textual (PDF, DOCX, XLSX, HTML, CSV). Reliability is *demonstrated*, not asserted:
deterministic gates (verbatim quotes, no compliant-without-evidence, schema validation), abstention, confidence calibration, and a
measurement harness. **Not D3:** OCR exists as a prototype fallback but multimodal reliability (scans, screenshots, images) is not measured.

## 18. F1/F2/F3 assessment

**Claim: F3** (≥ 8 features). All 14 features are implemented end-to-end and exercised by an automated end-to-end test; strength varies:
features 1–3, 5–9 and 11 are the strongest; 4, 10, 12, 13 are implemented with lighter evaluation; 14 is prototype-grade (flagged
candidates always go to a human; precision depends on the model).

**Measured with live free-tier models** (Gemini 2.5/3.5/3.8 Flash + Groq gpt-oss-120b / qwen3.8-27b, routed by
quota; `scripts.run_eval`, 2026-10-08):

| Metric | Value |
|---|---|
| Paragraph recovery / quote recoverability | 1.00 / 1.00 (108 paragraphs, 65 quotes) |
| Obligation extraction (65 golden, v1 + v2) | **P 0.89 · R 1.00 · F1 0.94**; modality 1.00, period 0.96, type 0.88 |
| Unverified-output rate (caught by the quote gate) | 0.037 · calibration error (ECE) 0.11 |
| Obligation → control mapping (35 golden links) | **P 0.87 · R 0.94 · F1 0.90** |
| Seeded-gap recall | **11 / 11** (18 further obligations flagged outside the seed set — see limitation) |
| Change detection v1 → v2 (material) | **P 1.00 · R 1.00**; editorial change (3.2) suppressed; only 5 changed paragraphs re-extracted |
| Retrieval Recall@1 / @3 / @5, MRR (25 queries) | **0.96 / 1.00 / 1.00, 0.973** (bge-base + bge-reranker, production hybrid search) |
| Conflict recall / clusters | both seeded conflicts flagged (6 candidates sent to review, precision vs seed 0.33); cluster recall 0.5 |
| Operations | 100 % agent-run success after fixes; 0 LLM failures; ~31 % cache hits; **$0.00 actual** (≈ $1.34 paid-tier equivalent for the whole seed) |

Limitations stated with the numbers: synthetic corpus and golden labels written by the same author; flagged-outside-seed
gaps are mostly legitimate weaknesses (e.g. controls without artefacts) but are not individually labelled; free-tier
quotas make throughput minutes-per-regulation, not seconds.

## 19. Demo flow (7 minutes)

See `docs/demo-script.md`: dashboard → upload bank documents & v1 regulations (trace) → obligation with verified quote → mapping →
effectiveness waterfall (stale evidence, insufficient evidence) → findings & risk → remediation approval → **upload KYC v2** (autonomous
impact analysis) → what-if → potential conflicts & clusters → reliability dashboard.

## 20. Future improvements

True BM25 (ParadeDB `pg_search`); shared quota counters in Postgres/Redis for multi-worker deployments; vision-based evidence assessment
(screenshots) and measured OCR reliability (D3); SSO/OIDC; encryption at rest; SLA tracking on review queues; larger, independently
labelled golden sets with inter-annotator agreement; RBI notification feed poller; fine-grained RLS on runs/LLM tables.

---

## Judge perspective (evidence, not claims)

| Criterion | Implementation | Demo evidence | Metric | Limitation |
|---|---|---|---|---|
| Significance | Full regulation→remediation lineage with monitoring | Impact report after v2 upload | 14/14 features exercised by E2E test | synthetic corpus |
| Innovation | Quote-verified extraction; amendment applier; simulation reusing production engines | Highlighted quotes; simulation diff | quote-gate rejection rate logged | amendment path lightly tested |
| Effective use of AI | LLM only for language; engines for numbers | Trace shows LLM vs tool steps | cost/call per step | free-tier model quality varies |
| Technical execution | Typed Python, strict mypy, 69 unit + 5 integration tests, migrations, RLS | `pytest`, `mypy app` | all green | — |
| Agentic capability | Supervisor planning with repair; retries, fallback, resume, budgets | Plan + decisions in trace | agent success rate (eval) | plans constrained to a catalogue (by design) |
| Business impact | Prioritised, owned, dated remediation; hours estimate in what-if | Remediation tracker | risk bands P1–P4 | weights are expert-set |
| Prototype quality | 18-page dashboard, Docker one-command start | live demo | — | single tenant in demo |
| Scalability | SKIP LOCKED queue, pgvector HNSW, stateless API | config switch for separate workers | — | quota counters per process |
| Responsible AI | human gates, abstention, redaction, disclaimers | review queue, audit verify | — | not legal advice |
| Robustness | provider fallback chain, circuit breaker, caching | `/health/llm` | cache hit rate | free-tier rate limits |
