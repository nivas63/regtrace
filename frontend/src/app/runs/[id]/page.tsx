"use client";

import { useParams } from "next/navigation";

import { Badge, Card, ErrorBox, Json, Loading, PageHeader, Stat } from "@/components/ui";
import { fmtDateTime, post, useApi } from "@/lib/api";

type Step = { id: string; step_name: string; attempt: number; status: string; agent: string | null; output: Record<string, unknown>;
  decisions: { decision: string; reason: string; [k: string]: unknown }[]; error: string | null; started_at: string; duration_ms: number | null };
type ToolCall = { id: string; step_id: string | null; agent: string; tool: string; args: Record<string, unknown>; status: string; latency_ms: number; error: string | null };
type LLMCall = { id: string; step_id: string | null; purpose: string; prompt_version: string; tier: string; provider: string | null; model: string | null;
  input_tokens: number; output_tokens: number; latency_ms: number; cache_hit: boolean; outcome: string; repairs: number;
  attempts: { provider: string; outcome: string; error?: string | null }[]; error: string | null };
type Trace = { id: string; workflow_type: string; status: string; plan: string[]; plan_rationale: string | null; replans: number; error: string | null;
  input: Record<string, unknown>; state: Record<string, unknown>; created_at: string; finished_at: string | null;
  steps: Step[]; tool_calls: ToolCall[]; llm_calls: LLMCall[];
  totals: { llm_calls: number; cache_hits: number; tokens: number; equivalent_cost_usd: number; tool_calls: number } };

export default function RunTrace() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading } = useApi<Trace>(`/agent-runs/${id}`, 3000);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} />;
  const done = new Set(data.steps.filter((s) => s.status === "SUCCEEDED" || s.status === "FALLBACK").map((s) => s.step_name));
  return (
    <>
      <PageHeader title={`Agent execution trace · ${data.workflow_type}`}
        subtitle={<>Run {data.id} · <Badge value={data.status} /> · started {fmtDateTime(data.created_at)}{data.finished_at ? ` · finished ${fmtDateTime(data.finished_at)}` : ""}</>} />
      {data.error ? (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div className="flex-1"><ErrorBox error={data.error} /></div>
          {data.status === "FAILED" ? (
            <button className="btn-primary" onClick={async () => { await post(`/agent-runs/${data.id}/retry`); }}>
              Retry from last completed step
            </button>
          ) : null}
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Steps executed" value={data.steps.length} />
        <Stat label="Tool calls" value={data.totals.tool_calls} />
        <Stat label="LLM calls (cached)" value={`${data.totals.llm_calls} (${data.totals.cache_hits})`} />
        <Stat label="Tokens" value={data.totals.tokens.toLocaleString()} />
        <Stat label="Actual / equivalent cost" value="$0.00" hint={`≈ $${data.totals.equivalent_cost_usd}`} tone="green" />
      </div>
      <Card title="Supervisor plan" className="mt-4">
        <div className="flex flex-wrap items-center gap-1">
          {data.plan.map((s, i) => (
            <span key={s} className="flex items-center gap-1">
              <span className={`rounded-md px-2 py-1 text-xs font-medium ring-1 ${done.has(s) ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-slate-50 text-ink-muted ring-line"}`}>{s}</span>
              {i < data.plan.length - 1 ? <span className="text-ink-muted">→</span> : null}
            </span>
          ))}
        </div>
        {data.plan_rationale || data.state.plan_rationale ? <p className="mt-2 text-sm text-ink-soft"><span className="font-semibold">Rationale:</span> {String(data.plan_rationale ?? data.state.plan_rationale)}</p> : null}
      </Card>
      <div className="mt-4 space-y-3">
        {data.steps.map((s) => {
          const tools = data.tool_calls.filter((t) => t.step_id === s.id);
          const calls = data.llm_calls.filter((c) => c.step_id === s.id);
          return (
            <Card key={s.id} title={<span className="flex items-center gap-2"><Badge value={s.status} /> {s.step_name} {s.attempt > 1 ? <Badge value={`attempt ${s.attempt}`} tone="amber" /> : null}
              <span className="text-xs font-normal text-ink-muted">{s.agent} · {s.duration_ms ? `${(s.duration_ms / 1000).toFixed(1)} s` : "…"}</span></span>}>
              {s.error ? <p className="mb-2 text-xs text-rose-600">{s.error}</p> : null}
              {s.decisions.length ? (
                <div className="mb-2 space-y-1">
                  {s.decisions.map((d, i) => <div key={i} className="text-sm"><Badge value={d.decision} tone="purple" /> {d.reason}</div>)}
                </div>
              ) : null}
              {tools.length ? <div className="mb-2 flex flex-wrap gap-1">{tools.map((t) => <span key={t.id} className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[11px]" title={JSON.stringify(t.args)}>{t.agent} → {t.tool}() <Badge value={t.status} tone={t.status === "OK" ? "green" : "red"} /> {Math.round(t.latency_ms)}ms</span>)}</div> : null}
              {calls.length ? (
                <table className="table mb-2">
                  <thead><tr><th>LLM call</th><th>Tier</th><th>Provider / model</th><th>Tokens</th><th>Latency</th><th>Outcome</th><th>Routing trail</th></tr></thead>
                  <tbody>{calls.map((c) => (
                    <tr key={c.id}>
                      <td className="text-xs">{c.purpose}@v{c.prompt_version}</td><td className="text-xs">{c.tier}</td>
                      <td className="text-xs">{c.cache_hit ? <Badge value="CACHE HIT" tone="green" /> : `${c.provider ?? "—"} / ${c.model ?? "—"}`}</td>
                      <td className="tabular-nums text-xs">{c.input_tokens}+{c.output_tokens}</td><td className="tabular-nums text-xs">{Math.round(c.latency_ms)}ms</td>
                      <td><Badge value={c.outcome} tone={c.outcome === "SUCCESS" ? "green" : "red"} />{c.repairs ? <span className="text-[10px] text-amber-700"> {c.repairs} repair</span> : null}</td>
                      <td className="text-[10px] text-ink-muted">{c.attempts.map((a) => `${a.provider}:${a.outcome}`).join(" → ")}</td>
                    </tr>))}</tbody>
                </table>
              ) : null}
              {Object.keys(s.output).length ? <details className="text-xs"><summary className="cursor-pointer text-ink-muted">Step output</summary><Json value={s.output} /></details> : null}
            </Card>
          );
        })}
      </div>
    </>
  );
}
