"use client";

import { useState } from "react";

import { A, Badge, Card, Empty, ErrorBox, Json, Loading, PageHeader, Quote, Tabs } from "@/components/ui";
import { post, useApi } from "@/lib/api";

type Finding = { id: string; gap_type: string; status: string; title: string; description: string; provisional: boolean;
  details: Record<string, unknown>; risk: { score: number; priority: string; reason: string; factors: Record<string, unknown> } | null;
  obligation: { id: string; section_path: string; requirement_text: string; source_quote: string; obligation_type: string };
  regulation_title: string; control: { id: string; control_key: string; name: string; business_unit: string | null } | null;
  remediation: { id: string; status: string; due_date: string | null } | null };
type Heatmap = { by_business_unit: Record<string, Record<string, number>>; by_obligation_type: Record<string, Record<string, number>>; total_open: number };

const PRIORITIES = ["P1", "P2", "P3", "P4"];
const CELL = ["bg-white", "bg-rose-100", "bg-rose-200", "bg-rose-300", "bg-rose-400 text-white", "bg-rose-600 text-white"];

function HeatmapView({ data }: { data: Heatmap }) {
  const units = Object.keys(data.by_business_unit);
  if (!units.length) return <Empty>No open gaps.</Empty>;
  return (
    <table className="table">
      <thead><tr><th>Business unit / function</th>{PRIORITIES.map((p) => <th key={p} className="text-center">{p}</th>)}</tr></thead>
      <tbody>
        {units.map((u) => (
          <tr key={u}><td>{u}</td>
            {PRIORITIES.map((p) => {
              const n = data.by_business_unit[u][p] ?? 0;
              return <td key={p} className={`text-center font-semibold tabular-nums ${CELL[Math.min(5, n)]}`}>{n || ""}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function FindingsPage() {
  const [tab, setTab] = useState("Open gaps");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const findings = useApi<Finding[]>(`/compliance/findings?status=${tab === "All findings" ? "ALL" : "OPEN"}`, 15000);
  const heatmap = useApi<Heatmap>("/risk/heatmap", 15000);
  async function reassess() {
    setBusy(true);
    try {
      const res = await post<{ run_id: string }>("/compliance/assess");
      setMessage(`Full assessment queued — run ${res.run_id.slice(0, 8)}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader title="Compliance findings & risk"
        subtitle="Gaps are classified by deterministic rules (missing / partial / ineffective control, stale / missing evidence, ambiguity, conflict, applicability). Risk = 100 × inherent/5 × likelihood × urgency; factors shown per finding."
        actions={<button className="btn-primary" onClick={reassess} disabled={busy}>Run full assessment</button>} />
      {message ? <p className="mb-3 text-sm text-ink-soft">{message}</p> : null}
      <Tabs tabs={["Open gaps", "Risk heatmap", "All findings"]} active={tab} onChange={setTab} />
      <ErrorBox error={findings.error} />
      {tab === "Risk heatmap" ? (
        heatmap.data ? <Card title={`Open gaps: ${heatmap.data.total_open}`}><HeatmapView data={heatmap.data} /></Card> : <Loading />
      ) : findings.loading && !findings.data ? <Loading /> : !findings.data?.length ? <Empty>No findings.</Empty> : (
        <div className="space-y-3">
          {findings.data.map((f) => (
            <Card key={f.id}>
              <div className="flex flex-wrap items-start gap-3">
                <div className="w-20 text-center">
                  <Badge value={f.risk?.priority} />
                  <div className="mt-1 text-lg font-semibold tabular-nums">{f.risk?.score ?? "—"}</div>
                  <div className="text-[10px] text-ink-muted">risk score</div>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge value={f.gap_type} tone="red" /> <Badge value={f.status} /> {f.provisional ? <Badge value="PROVISIONAL" tone="amber" /> : null}
                    {Boolean(f.details.carried_over) ? <Badge value="CARRIED OVER" tone="gray" /> : null}
                    <span className="font-medium">{f.title}</span>
                  </div>
                  <div className="mt-1 text-xs text-ink-muted">
                    <A href={`/obligations/${f.obligation.id}`}>{f.regulation_title} · para {f.obligation.section_path}</A>
                    {f.control ? <> · <A href={`/controls/${f.control.id}`}>{f.control.control_key}</A> ({f.control.business_unit})</> : null}
                    {f.remediation ? <> · remediation <Badge value={f.remediation.status} /></> : null}
                  </div>
                  <div className="mt-2"><Quote>{f.obligation.source_quote}</Quote></div>
                  {f.risk ? <p className="mt-2 text-xs text-ink-soft">Why this priority: {f.risk.reason}</p> : null}
                  <details className="mt-1 text-xs"><summary className="cursor-pointer text-ink-muted">Risk factors & gap details</summary>
                    <div className="grid gap-2 md:grid-cols-2"><Json value={f.risk?.factors ?? {}} /><Json value={f.details} /></div></details>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
