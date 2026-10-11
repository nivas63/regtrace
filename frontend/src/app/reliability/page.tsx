"use client";

import { useState } from "react";

import { Badge, Card, Empty, ErrorBox, Json, Loading, PageHeader, Stat } from "@/components/ui";
import { fmtDateTime, post, useApi } from "@/lib/api";

type EvalRun = { id: string; suite: string; metrics: Record<string, unknown>; created_at: string; notes: string | null };
type Obs = { llm: { purpose: string; provider: string | null; outcome: string; calls: number; input_tokens: number; output_tokens: number;
  avg_latency_ms: number; cache_hits: number; equivalent_cost_usd: number; repairs: number }[];
  tools: { tool: string; status: string; calls: number; avg_latency_ms: number }[]; runs_by_status: Record<string, number>; providers: string[];
  tool_catalogue: { name: string; description: string; agents: string[]; side_effects: boolean }[] };

const HEADLINE: [string, string, string][] = [
  ["parsing", "paragraph_recovery", "Paragraph recovery"], ["extraction", "precision", "Obligation precision"],
  ["extraction", "recall", "Obligation recall"], ["mapping", "recall", "Mapping recall"], ["gaps", "recall", "Seeded-gap recall"],
  ["retrieval", "recall@5", "Retrieval Recall@5"], ["change", "f1", "Change-detection F1"], ["cross_regulation", "conflict_recall", "Conflict recall"],
];

export default function ReliabilityPage() {
  const evals = useApi<EvalRun[]>("/eval/runs");
  const obs = useApi<Obs>("/observability/llm", 15000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latest: Record<string, EvalRun> = {};
  for (const r of evals.data ?? []) if (!latest[r.suite]) latest[r.suite] = r;
  async function run() {
    setBusy(true);
    setError(null);
    try {
      await post("/eval/run");
      await evals.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  const metric = (suite: string, key: string) => {
    const value = latest[suite]?.metrics?.[key];
    return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
  };
  return (
    <>
      <PageHeader title="Reliability, observability & cost"
        subtitle="Measured against golden labels (not claimed). Limitations: small synthetic golden set written by the corpus author; OCR reliability not measured."
        actions={<button className="btn-primary" onClick={run} disabled={busy}>{busy ? "Evaluating…" : "Run evaluation"}</button>} />
      <ErrorBox error={error} />
      {evals.loading && !evals.data ? <Loading /> : !Object.keys(latest).length ? <Empty>No evaluation yet — click “Run evaluation”.</Empty> : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {HEADLINE.map(([suite, key, label]) => <Stat key={label} label={label} value={metric(suite, key)} hint={latest[suite] ? fmtDateTime(latest[suite].created_at) : undefined} />)}
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {Object.values(latest).map((r) => <Card key={r.id} title={r.suite}><Json value={r.metrics} /></Card>)}
          </div>
        </>
      )}
      <h2 className="mb-2 mt-8 text-base font-semibold">Operations</h2>
      {obs.data ? (
        <>
          <div className="mb-3 flex flex-wrap gap-2 text-xs">Providers configured: {obs.data.providers.length ? obs.data.providers.map((p) => <Badge key={p} value={p} tone="blue" />) : <Badge value="NONE" tone="red" />}
            <span className="ml-3">Runs: {Object.entries(obs.data.runs_by_status).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(" · ")}</span></div>
          <Card title="LLM calls by purpose / provider / outcome">
            <table className="table"><thead><tr><th>Purpose</th><th>Provider</th><th>Outcome</th><th>Calls</th><th>Cache hits</th><th>Tokens in/out</th><th>Avg latency</th><th>Repairs</th><th>Equivalent $</th></tr></thead>
              <tbody>{obs.data.llm.map((r, i) => <tr key={i}><td>{r.purpose}</td><td>{r.provider ?? "—"}</td><td><Badge value={r.outcome} tone={r.outcome === "SUCCESS" ? "green" : "red"} /></td>
                <td className="tabular-nums">{r.calls}</td><td className="tabular-nums">{r.cache_hits}</td><td className="tabular-nums text-xs">{r.input_tokens}/{r.output_tokens}</td>
                <td className="tabular-nums text-xs">{r.avg_latency_ms} ms</td><td className="tabular-nums">{r.repairs}</td><td className="tabular-nums text-xs">{r.equivalent_cost_usd}</td></tr>)}</tbody></table>
          </Card>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card title="Tool calls">
              <table className="table"><thead><tr><th>Tool</th><th>Status</th><th>Calls</th><th>Avg latency</th></tr></thead>
                <tbody>{obs.data.tools.map((t, i) => <tr key={i}><td className="font-mono text-xs">{t.tool}</td><td><Badge value={t.status} tone={t.status === "OK" ? "green" : "red"} /></td><td>{t.calls}</td><td className="text-xs">{t.avg_latency_ms} ms</td></tr>)}</tbody></table>
            </Card>
            <Card title="Tool catalogue (permissioned)">
              {obs.data.tool_catalogue.map((t) => <div key={t.name} className="border-b border-line py-1.5 text-xs last:border-0"><span className="font-mono font-semibold">{t.name}</span> {t.side_effects ? <Badge value="WRITES" tone="amber" /> : null} — {t.description}<div className="text-ink-muted">allowed: {t.agents.join(", ")}</div></div>)}
            </Card>
          </div>
        </>
      ) : <Loading />}
    </>
  );
}
