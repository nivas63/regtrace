"use client";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { fmtDateTime, useApi } from "@/lib/api";

type Run = { id: string; workflow_type: string; status: string; current_step: string | null; plan: string[]; llm_calls_used: number;
  llm_call_budget: number; replans: number; error: string | null; created_at: string; finished_at: string | null; triggered_by: string };

export default function RunsPage() {
  const { data, error, loading } = useApi<Run[]>("/agent-runs?limit=100", 5000);
  return (
    <>
      <PageHeader title="Agent runs" subtitle="Durable workflow executions: plan, step attempts, decisions, tool calls and model calls are all persisted and replayable." />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty>No runs yet.</Empty> : (
        <Card>
          <table className="table">
            <thead><tr><th>Run</th><th>Workflow</th><th>Status</th><th>Step</th><th>Plan length</th><th>LLM budget</th><th>Triggered by</th><th>Started</th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id}>
                  <td className="font-mono text-xs"><A href={`/runs/${r.id}`}>{r.id.slice(0, 8)}</A></td>
                  <td>{r.workflow_type}</td>
                  <td><Badge value={r.status} />{r.error ? <div className="max-w-xs truncate text-[10px] text-rose-600" title={r.error}>{r.error}</div> : null}</td>
                  <td className="text-xs text-ink-muted">{r.current_step ?? "—"}</td>
                  <td className="tabular-nums">{r.plan.length}{r.replans ? ` (${r.replans} replan)` : ""}</td>
                  <td className="tabular-nums text-xs">{r.llm_calls_used}/{r.llm_call_budget}</td>
                  <td className="text-xs text-ink-muted">{r.triggered_by.startsWith("user:") ? "user" : r.triggered_by}</td>
                  <td className="text-xs text-ink-muted">{fmtDateTime(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
