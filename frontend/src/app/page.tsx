"use client";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "@/components/ui";
import { fmtDateTime, pct, useApi } from "@/lib/api";

type Summary = {
  obligations: number; applicable_obligations: number; controls: number; compliance_coverage: number | null;
  obligations_without_open_gaps: number; open_gaps_by_priority: Record<string, number>;
  open_gaps_by_type: Record<string, number>; control_ratings: Record<string, number>; pending_reviews: number;
  pending_conflicts: number; material_changes: number; remediation_by_status: Record<string, number>;
  recent_impacts: { id: string; summary: string; created_at: string }[];
  recent_runs: { id: string; workflow_type: string; status: string; current_step: string; created_at: string }[];
  llm: { calls: number; tokens: number; equivalent_cost_usd: number; cache_hits: number; actual_cost_usd: number };
  disclaimer: string;
};

function Bars({ data, order }: { data: Record<string, number>; order?: string[] }) {
  const keys = order ?? Object.keys(data);
  const max = Math.max(1, ...keys.map((k) => data[k] ?? 0));
  if (!keys.some((k) => data[k])) return <Empty>No data yet.</Empty>;
  return (
    <div className="space-y-2">
      {keys.map((k) => (
        <div key={k} className="flex items-center gap-3 text-sm">
          <div className="w-44 shrink-0"><Badge value={k} /></div>
          <div className="h-2 flex-1 rounded-full bg-slate-100">
            <div className="h-2 rounded-full bg-accent" style={{ width: `${((data[k] ?? 0) / max) * 100}%` }} />
          </div>
          <div className="w-8 text-right tabular-nums">{data[k] ?? 0}</div>
        </div>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const { data, error, loading } = useApi<Summary>("/dashboard/summary", 10000);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} />;
  const p = data.open_gaps_by_priority;
  return (
    <>
      <PageHeader title="Executive dashboard" subtitle={data.disclaimer} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Active obligations" value={data.obligations} hint={`${data.applicable_obligations} applicable to the bank`} />
        <Stat label="Obligations without open gaps" value={pct(data.compliance_coverage)} hint={`${data.obligations_without_open_gaps} of ${data.obligations}`}
          tone={(data.compliance_coverage ?? 0) >= 0.8 ? "green" : "amber"} />
        <Stat label="P1 / P2 gaps" value={`${p.P1 ?? 0} / ${p.P2 ?? 0}`} tone={(p.P1 ?? 0) > 0 ? "red" : undefined} hint="Deterministic risk engine" />
        <Stat label="Pending human reviews" value={data.pending_reviews} hint={`${data.pending_conflicts} potential conflicts`} tone={data.pending_reviews ? "amber" : undefined} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card title="Open gaps by priority"><Bars data={p} order={["P1", "P2", "P3", "P4"]} /></Card>
        <Card title="Open gaps by type"><Bars data={data.open_gaps_by_type} /></Card>
        <Card title="Control effectiveness (per obligation mapping)">
          <Bars data={data.control_ratings} order={["EFFECTIVE", "PARTIALLY_EFFECTIVE", "INEFFECTIVE", "INSUFFICIENT_EVIDENCE"]} />
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Latest regulatory impact analyses" actions={<A href="/impact">All</A>}>
          {data.recent_impacts.length ? (
            <ul className="space-y-3">
              {data.recent_impacts.map((i) => (
                <li key={i.id} className="text-sm">
                  <A href={`/impact/${i.id}`}>{fmtDateTime(i.created_at)}</A>
                  <p className="mt-0.5 text-ink-soft">{i.summary}</p>
                </li>
              ))}
            </ul>
          ) : <Empty>Upload a regulation to trigger autonomous impact analysis.</Empty>}
        </Card>
        <Card title="Agent activity" actions={<A href="/runs">Trace</A>}>
          <table className="table">
            <thead><tr><th>Workflow</th><th>Status</th><th>Step</th><th>Started</th></tr></thead>
            <tbody>
              {data.recent_runs.map((r) => (
                <tr key={r.id}>
                  <td><A href={`/runs/${r.id}`}>{r.workflow_type}</A></td>
                  <td><Badge value={r.status} /></td>
                  <td className="text-ink-muted">{r.current_step ?? "—"}</td>
                  <td className="text-ink-muted">{fmtDateTime(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Material regulatory changes" value={data.material_changes} />
        <Stat label="Remediation plans" value={Object.values(data.remediation_by_status).reduce((a, b) => a + b, 0)}
          hint={Object.entries(data.remediation_by_status).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(" · ") || "none"} />
        <Stat label="LLM calls (cache hits)" value={`${data.llm.calls} (${data.llm.cache_hits})`} hint={`${data.llm.tokens.toLocaleString()} tokens`} />
        <Stat label="Actual / equivalent cost" value={`$${data.llm.actual_cost_usd.toFixed(2)}`} hint={`≈ $${data.llm.equivalent_cost_usd.toFixed(3)} on a paid tier`} tone="green" />
      </div>
    </>
  );
}
