# RegTrace

**Agentic regulatory compliance intelligence for banks.**
Regulations → obligations → applicability → controls → evidence → tests → gaps → risk → remediation →
monitoring, kept as one traceable, versioned graph — and re-analysed autonomously whenever a regulation changes.

> AI-assisted compliance assessment. Not legal advice. Humans remain the authority on regulatory interpretation.
> The demo bank ("Aravali Bank Ltd") and all demo regulations are **SYNTHETIC**, attributed to fictional authorities.

ET × Accenture AI Hackathon (Agentic Edition), Problem 1 · self-assessed **F3 / D2** · runs at **US$0**.

- Architecture & solution document: [docs/architecture.md](docs/architecture.md)
- Demo script: [docs/demo-script.md](docs/demo-script.md)
- Decision log: [docs/adr/decision-log.md](docs/adr/decision-log.md)

## Quick start (Docker)

```powershell
cd regtrace
Copy-Item .env.example .env      # add GEMINI_API_KEY and/or GROQ_API_KEY (both free)
docker compose up -d --build     # postgres + api (migrations, model warm-up) + web
docker compose exec api python -m scripts.generate_demo_data --out /data/corpus   # only if data/ is empty
docker compose exec api python -m scripts.seed_demo        # synthetic bank + 3 regulations (uses the LLM)
docker compose exec api python -m scripts.run_eval         # metrics vs golden labels
```

Rebuild a clean demo in minutes without spending quota (restores `data/llm_cache_snapshot.sql`):
`.\reset-demo.ps1` (or `.\reset-demo.ps1 -WithV2`).

Open http://localhost:3000 and sign in as `analyst@aravali.example` / `reviewer@aravali.example` /
`auditor@aravali.example` with the `SEED_PASSWORD` from `.env` (default `RegTrace!2026`).
API docs: http://localhost:8000/api/v1/docs. For the live demo, upload
`data/synthetic/regulations/SYN_KYC_Master_Direction_2025_v2.pdf` as a REGULATION on the Documents page.

Without API keys everything that does not need a language model still works (ingestion, retrieval, control import,
scoring, gap and risk engines); LLM steps fail cleanly and open review items.

## Free LLM providers

| Tier | Chain |
|---|---|
| reasoning | Gemini 3.5 Flash → 2.5 Flash → 3.7 Flash → 3.8 Flash → Groq gpt-oss-120b → Groq qwen3.8-27b (→ Ollama) |
| fast | Gemini 3.5 Flash-Lite → 3.1 Flash-Lite → flash-lite-latest → Groq gpt-oss-20b (→ Ollama) |

Free-tier quotas are **per model** (e.g. Gemini 3.5 Flash: 20 requests/day; Groq: 1000/day and 8k tokens/minute per
model), so the router walks the chain and, when every model is momentarily throttled, waits for the soonest window.

Quota limits are tracked client-side (`LLM_RATE_LIMITS`); responses are cached in Postgres, so re-running the seed or
the demo costs no additional quota. Check your keys with `docker compose exec api python -m scripts.llm_smoke`.

## Development (host)

```powershell
cd backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# Windows Application Control may block compiled wheels:
pip install --force-reinstall --no-binary mypy mypy
docker compose -f ..\docker-compose.yml up -d postgres
alembic upgrade head
pytest              # 69 unit + 5 integration tests (integration needs the postgres container)
ruff check . ; mypy app
cd ..\frontend; npm install; npm run build
```

Integration tests create a separate `regtrace_test` database and use a scripted test LLM and a hashing embedder, so
they need no network. Real-model quality is measured by `scripts.run_eval` and `scripts.eval_retrieval`.

## Repository layout

```
backend/
  app/
    agents/            LLM agents (regulatory, change, controls, analysis, simulation, supervisor) + output schemas
    api/v1/            REST routers (auth, documents, regulations, controls, compliance, analysis, governance, eval)
    domain/            pure logic: vocabularies, frequency normalisation, quote verification, scoring engines
    ingestion/         validation, parsers, structure recovery, metadata, chunking
    retrieval/         local embeddings/reranker, hybrid search (dense + lexical + graph, RRF)
    rag/               grounded question answering
    knowledge_graph/   typed time-valid edges, recursive traversal
    llm/               provider interface, Gemini + OpenAI-compatible adapters, router, cache/budget service, prompts
    workflows/         durable state-machine engine, tool registry, workflow definitions
    services/          ingestion, assessment engines, reporting, reviews, audit, seeding
    security/          passwords, JWT, RBAC, PII redaction, prompt-injection scanner
    evaluation/        golden-label evaluation harness
    models/ db/        SQLAlchemy models, sessions with row-level-security context
  migrations/          Alembic (schema, RLS/roles, graph uniqueness)
  prompts/             versioned prompts (version is part of the cache key)
  scripts/             corpus generator, seeding, evaluation, smoke tests
  tests/               unit + integration (RLS, audit tamper, full pipeline, API)
frontend/              Next.js dashboard (18 pages)
data/synthetic/        generated SYNTHETIC regulations, bank policies, control matrix, tests, evidence
data/golden/           evaluation labels
docs/                  architecture, demo script, ADRs
```
