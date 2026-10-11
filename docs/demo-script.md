# RegTrace — 7-minute demo script

**Before the demo (day before):**
1. Add free keys to `.env` (`GEMINI_API_KEY`, `GROQ_API_KEY`), then `docker compose up -d`.
2. Seed the synthetic bank and the three v1 regulations: `.\reset-demo.ps1` (restores the LLM cache snapshot, so
   it replays mostly from cache; a cold seed takes ~25 min on free tiers because of per-model quotas).
3. Run once end-to-end *including* the v2 upload on a scratch copy if you want the cache warm for the live step,
   or keep v2 for the live moment (recommended; it takes 1–3 minutes on free tiers).
4. `docker compose exec api python -m scripts.run_eval` → numbers appear on the Reliability page.
5. Log in at http://localhost:3000 as `analyst@aravali.example` (password = `SEED_PASSWORD`); keep a second
   browser profile logged in as `reviewer@aravali.example` for approvals.

| Time | Screen | Say / show |
|---|---|---|
| 0:00 | Executive dashboard | "RegTrace maintains regulation → obligation → control → evidence → gap → remediation as one traceable graph. Everything here is SYNTHETIC; outputs are AI-assisted, humans decide." Point at P1/P2 gaps, pending reviews, $0 actual cost. |
| 0:40 | Documents → a regulation run → **Agent trace** | Plan chosen by the supervisor with rationale; each step with decisions, tool calls (score_controls, identify_gaps, calculate_risk) and LLM calls (provider, tokens, cache hits, routing trail). |
| 1:30 | Regulations → KYC → text view | Obligations highlighted inside the paragraph text. Open one: verbatim quote verified against the source, confidence factors (quote match, verifier, self-confidence), lineage key, prompt version. |
| 2:10 | Mapping matrix | Obligation 6.2 → C-KYC-05: relation, coverage, reason naming the frequencies compared, verified control quote, PROPOSED/APPROVED status. |
| 2:40 | Controls → C-KYC-13 and C-IT-01 | C-KYC-13: last internal audit 275 days ago against a quarterly requirement → recency 0 → **STALE_EVIDENCE**. C-IT-01 (MFA): no test, no evidence → **INSUFFICIENT_EVIDENCE**, "never compliant without evidence". Show the deterministic factor vector. |
| 3:20 | Findings & risk → heatmap | Gap types, deterministic risk score with "why this priority". Remediation tracker: P1 plan requires approval; switch to the reviewer and approve (audit-logged, segregation of duties enforced). |
| 4:00 | **Documents → upload `SYN_KYC_Master_Direction_2025_v2.pdf` (REGULATION)** | Autonomous impact analysis: open the run trace live — version linked via "in supersession of SYN/KYC/2024/01", diff (6.2 six→three months, 7.1 two years→one year, 11.2 30→15 days, new 8.3, 3.2 editorial suppressed), incremental extraction of changed paragraphs only, lineage carried forward, re-mapping, re-scoring, new gaps, remediation, impact report. |
| 5:20 | Impact report | "What changed → obligations → controls impacted → evidence → new gaps (C-KYC-05 now runs less often than required) → remediation → departments." |
| 5:50 | What-if simulation | "What if disaster recovery drills must be performed every quarter?" → C-IT-05 annual control no longer adequate, new gap and risk, extra hours per year, departments. Baseline untouched. |
| 6:20 | Cross-regulation & conflicts | Potential conflict: KYC record retention (5 years after relationship ends) vs data-protection erasure (3 years after closure) **with the "unless required by law" exception surfaced as a reconciliation hint**; log retention 180 days vs 90-day cap flagged for human review. Clusters: annual training → shared control suggestion. |
| 6:45 | Reliability page | Parsing 100 %, retrieval Recall@5, extraction/mapping P/R, seeded-gap recall, cache hit rate, equivalent cost — measured against golden labels, with stated limitations. Close on F3/D2. |

**If the live v2 upload is slow:** show a previously completed v2 run trace (Agent runs page) — nothing is faked; it is the stored
execution record.
