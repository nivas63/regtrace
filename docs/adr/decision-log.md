# RegTrace – Architecture Decision Log

Each record: **Decision · Options · Chosen · Reason · Trade-offs · Revisit when**.
Status is `Accepted` unless noted. New decisions are appended, never rewritten;
a reversed decision gets a new record that supersedes the old one.

---

## ADR-001 Orchestration: custom durable state machine
- **Options:** LangGraph · CrewAI / AutoGen · custom state machine persisted in Postgres
- **Chosen:** Custom state machine (~600 LOC), steps persisted in `workflow_steps`
- **Reason:** Compliance needs replayable, auditable, bounded execution. Explicit
  transitions and per-step idempotency keys; no hidden framework prompts; audit rows
  written in the same transaction as domain changes; easy to explain to reviewers.
- **Trade-offs:** We own checkpointing/resume code.
- **Revisit when:** >15 workflow types or dynamic sub-graphs → LangGraph with a Postgres checkpointer.

## ADR-002 Vector store: pgvector
- **Options:** Qdrant · Pinecone · pgvector
- **Chosen:** pgvector (HNSW) inside PostgreSQL 16
- **Reason:** Tenant/version/effective-date filters and vector search in one SQL
  query, transactional with metadata, one fewer service.
- **Trade-offs:** Weaker than dedicated engines at very large scale.
- **Revisit when:** >5M chunks or high QPS → Qdrant.

## ADR-003 Primary database: PostgreSQL
- **Options:** MongoDB · PostgreSQL
- **Chosen:** PostgreSQL 16
- **Reason:** Lineage (regulation→obligation→control→evidence) needs foreign keys;
  row-level security for tenant isolation; JSONB for flexible attributes; recursive CTEs.
- **Trade-offs:** Schema migrations required (Alembic).
- **Revisit when:** Not expected.

## ADR-004 Knowledge graph: typed edges table in Postgres
- **Options:** Neo4j · Apache AGE · relational `kg_edges` + recursive CTEs
- **Chosen:** `kg_edges` with time-valid edges
- **Reason:** Queries are ≤4 hops (impact propagation); same transaction as domain
  data; no synchronisation lag between stores.
- **Trade-offs:** No Cypher; heavy graph algorithms are awkward.
- **Revisit when:** Graph analytics (centrality, community detection) at scale → Apache AGE / Neo4j.

## ADR-005 Lexical retrieval: Postgres full-text search
- **Options:** Elasticsearch · ParadeDB `pg_search` (true BM25) · Postgres FTS
- **Chosen:** Postgres FTS (`ts_rank_cd`). **Not true BM25** – stated as such.
- **Reason:** Zero extra infrastructure; exact matches on circular numbers and paragraph references.
- **Trade-offs:** Ranking quality below BM25.
- **Revisit when:** Retrieval evaluation shows the lexical leg underperforming → `pg_search`.

## ADR-006 RAG + structured extraction instead of fine-tuning
- **Options:** Fine-tune a model · RAG with schema-validated extraction
- **Chosen:** RAG + structured extraction
- **Reason:** Regulations change; citations are mandatory; no labelled corpus exists.
- **Trade-offs:** More tokens per call.
- **Revisit when:** Stable, high-volume extraction → distil into a small model.

## ADR-007 Eight focused agents + deterministic engines
- **Options:** One general agent · 14 agents (one per feature) · 8 agents + engines
- **Chosen:** 8 LLM agents; effectiveness, gaps, risk, simulation propagation and
  reporting are deterministic engines.
- **Reason:** An agent only where autonomous language reasoning is needed; every
  number is reproducible code.
- **Trade-offs:** More orchestration code.

## ADR-008 LLM for language, rules for anything checkable
- **Chosen:** LLM proposes → deterministic verification → human disposes.
- **Reason:** Auditability; hallucination control (verbatim-quote gate, citation closure).

## ADR-009 Zero-cost LLM providers (supersedes the paid-model default from Phase 0)
- **Options:** Paid Claude/OpenAI · Gemini free tier · Groq free tier · local Ollama
- **Chosen:** Chain per tier: Gemini (Flash / Flash-Lite) → Groq → Ollama, behind
  `LLMProvider`. Two adapters: Gemini REST and OpenAI-compatible (Groq + Ollama).
- **Reason:** Project constraint is US$0. Gemini has the largest free quota and native
  JSON-schema output; Groq is a fast fallback; Ollama is unlimited but slow on CPU.
- **Trade-offs:** Daily request quotas (not money) become the bottleneck → client-side
  rate limiter, response cache, batching and deterministic prefilters are mandatory.
  Free-tier inputs may be used by the provider to improve its products → only public
  regulatory documents and synthetic bank data are sent.
- **Revisit when:** Real bank data → local model or an enterprise tier with a no-training
  guarantee (configuration change only).

## ADR-010 Job queue in Postgres
- **Options:** Celery + Redis · arq · Postgres `SELECT … FOR UPDATE SKIP LOCKED`
- **Chosen:** Postgres queue
- **Reason:** One fewer service; jobs are transactional with workflow state.
- **Revisit when:** >50 jobs/s → Redis Streams / SQS.

## ADR-011 LLM HTTP via httpx, not vendor SDKs
- **Options:** google-genai / groq SDKs · direct REST with one shared httpx client
- **Chosen:** Direct REST
- **Reason:** One timeout/retry/connection-pool policy for every provider; error
  classification in one place; HTTP-level tests with respx; fewer dependencies.
- **Trade-offs:** We track API shape changes ourselves (covered by the smoke script).

## ADR-012 Local object storage behind an interface
- **Options:** MinIO in compose · local filesystem adapter
- **Chosen:** Filesystem adapter now; S3/MinIO adapter is a later config switch.
- **Reason:** Solo build – fewer containers to operate.

## ADR-013 In-process worker by default
- **Options:** separate worker service · worker loop inside the API process
- **Chosen:** In-process worker (`WORKER_IN_PROCESS=true`); standalone `python -m app.workers.worker` supported.
- **Reason:** One process = one accurate view of free-tier quota (the rate limiter is in memory) and one fewer
  container for a solo build. The queue is still Postgres `SKIP LOCKED`, so extra workers can be added.
- **Trade-offs:** API and background work share CPU; quota counters are per process.
- **Revisit when:** more than one worker → move quota counters into Postgres/Redis first.

## ADR-014 Batch LLM work per chapter / per 3 obligations
- **Chosen:** Extraction batches consecutive paragraphs of a chapter (≤ 3k tokens); mapping adjudicates 3
  obligations per call; evidence assessment is one call per control; remediation 2 findings per call.
- **Reason:** Free tiers are limited by requests/day (Gemini Flash ~250 RPD); batching cuts calls ~5x.
- **Trade-offs:** Larger prompts; one malformed item can force a repair round for the batch.

## ADR-015 Verbatim-quote gate as the primary hallucination control
- **Chosen:** Every obligation / evidence verdict / mapping rationale / policy control must carry a quote verified
  deterministically (normalised exact match, fuzzy ≥ 0.92). Unverified items are rejected or downgraded.
- **Reason:** Cheap, model-independent, and it converts "trust the model" into "check the model".
- **Trade-offs:** Paraphrased-but-correct outputs are rejected (lower recall); measured as unverified-output rate.

## ADR-016 Supervisor planning constrained to a catalogue
- **Options:** free-form ReAct agent · fixed pipeline · LLM plan over an allow-listed step catalogue
- **Chosen:** LLM proposes an ordered subset of catalogue steps; code enforces preconditions, canonical order and
  required steps (re-inserted and logged as `plan_repaired`); deterministic template when no model is available.
- **Reason:** Real planning decisions (which analyses to run for this document) with auditable, bounded behaviour.
- **Trade-offs:** The agent cannot invent new analyses — deliberate for a regulated domain.

## ADR-017 Deterministic test doubles for LLM and embeddings
- **Chosen:** Tests use a scripted OracleProvider (answers from golden labels) and a hashing embedder.
- **Reason:** Exercise every DB path, gate and workflow transition without network or quota; CI-friendly.
- **Trade-offs:** Tests validate pipeline logic, not model quality. Model quality is measured separately by
  `scripts.run_eval` (real LLM) and `scripts.eval_retrieval` (real embeddings). Never registered by the app.

## ADR-018 Frontend: Next.js + Tailwind, no component library
- **Chosen:** Next.js (App Router, standalone output), Tailwind, small in-house components; no chart library.
- **Reason:** Minimal dependencies for a solo build; pages are tables, cards and traces.
- **Revisit when:** richer visualisations (graph explorer) are needed → add React Flow.

## ADR-019 Local ONNX models for embeddings and reranking
- **Options:** API embeddings · sentence-transformers (torch) · fastembed (ONNX)
- **Chosen:** fastembed with BAAI/bge-base-en-v1.5 and BAAI/bge-reranker-base, inside the Linux container.
- **Reason:** $0, no torch (smaller image), CPU-friendly; measured Recall@5 = 1.0 / MRR 0.908 on the golden queries.
- **Trade-offs:** ~0.6 GB first-run download; ONNX Runtime was blocked by Application Control on the dev
  Windows host, so models run in Docker only (tests use the hashing embedder).

## ADR-020 Per-model fallback chains (free-tier quotas are per model)
- **Context (measured):** Gemini 3.5 Flash free tier = 20 requests/day; 2.5-flash-lite unavailable to new users;
  Groq = 1000 RPD / 8000 TPM per model. One model per provider cannot complete a seed in a day.
- **Chosen:** Each provider expands into several router entries (`gemini`, `gemini#2`…), one per model; quota is
  keyed by family + model. Reasoning: Gemini 3.5 → 2.5 → 3.7 → 3.8 Flash → Groq gpt-oss-120b → qwen3.8-27b.
- **Trade-offs:** Output quality varies by model; every call records which model answered (trace + llm_calls).

## ADR-021 Short prompt aliases instead of UUIDs
- **Context (measured):** Groq models returned the human-readable source label ("obligation_6.2") instead of the
  UUID id, so 164 of 170 mapping judgements were silently unmatched (mapping precision 0.26).
- **Chosen:** Items are labelled O1/F1/E1…; answers are resolved by alias, original id or an unambiguous hint; an
  ambiguous hint resolves to nothing. After the fix, mapping F1 rose from 0.41 to 0.90.

## ADR-022 Idempotent step retries
- **Context (measured):** Extraction commits per batch; three failed attempts left partial rows, producing 60
  obligation rows for 25 distinct obligations.
- **Chosen:** Steps that write in several transactions delete their own earlier-attempt rows (same run id) first.
  Covered by an integration test that injects a mid-step outage.

## ADR-023 Re-load rows in the writing session
- **Context (measured):** Mapping updates were applied to ORM objects loaded in an earlier, closed session and
  were silently discarded — re-adjudication never changed existing links.
- **Chosen:** Rows that will be modified are re-queried inside the transaction that writes them. Covered by an
  integration test that tampers with a mapping and checks that re-assessment restores it.
