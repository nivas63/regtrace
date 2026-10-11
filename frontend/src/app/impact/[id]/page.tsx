"use client";

import { useParams } from "next/navigation";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "@/components/ui";
import { fmtDate, useApi } from "@/lib/api";

type Report = {
  relationship: string | null; summary: string;
  changes: { section_path: string; change_type: string; category: string; is_material: boolean; summary: string }[];
  obligations: { id: string; section_path: string; type: string; requirement: string; frequency: string | null }[];
  impacted_controls: { id: string; control_key: string; name: string; owner: string; business_unit: string; via_obligations: string[] }[];
  impacted_evidence: number;
  findings: { id: string; gap_type: string; title: string; status: string; section_path: string; control_key: string | null; risk_score: number | null; priority: string | null; is_new: boolean }[];
  remediation: { id: string; priority: string; status: string; owner: string; due_date: string | null; requires_approval: boolean; root_cause: string }[];
  impacted_business_units: string[]; resolved_findings: number;
};

export default function ImpactDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading } = useApi<{ id: string; run_id: string; created_at: string; report: Report }>(`/impact/${id}`);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} />;
  const r = data.report;
  const material = r.changes.filter((c) => c.is_material);
  const newFindings = r.findings.filter((f) => f.is_new);
  return (
    <>
      <PageHeader title="Impact report" subtitle={r.summary} actions={<A href={`/runs/${data.run_id}`}>Agent execution trace →</A>} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label="Material changes" value={material.length} hint={`${r.changes.length - material.length} editorial`} />
        <Stat label="Obligations" value={r.obligations.length} />
        <Stat label="Controls impacted" value={r.impacted_controls.length} />
        <Stat label="Evidence impacted" value={r.impacted_evidence} />
        <Stat label="New gaps" value={newFindings.length} tone={newFindings.length ? "red" : "green"} hint={`${r.resolved_findings} resolved`} />
        <Stat label="Remediation plans" value={r.remediation.length} />
      </div>
      {r.changes.length ? (
        <Card title="What changed" className="mt-4">
          <table className="table"><thead><tr><th>Para</th><th>Change</th><th>Category</th><th>Summary</th></tr></thead>
            <tbody>{r.changes.map((c) => <tr key={c.section_path + c.change_type}><td className="font-mono">{c.section_path}</td><td><Badge value={c.change_type} /> {c.is_material ? <Badge value="MATERIAL" tone="red" /> : null}</td><td><Badge value={c.category} tone="gray" /></td><td className="text-sm">{c.summary}</td></tr>)}</tbody></table>
        </Card>
      ) : null}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Impacted controls">
          {!r.impacted_controls.length ? <Empty>None.</Empty> : r.impacted_controls.map((c) => (
            <div key={c.id} className="border-b border-line py-2 text-sm last:border-0">
              <A href={`/controls/${c.id}`}>{c.control_key}</A> {c.name}
              <div className="text-xs text-ink-muted">{c.owner} · {c.business_unit} · via para {c.via_obligations.join(", ")}</div>
            </div>
          ))}
        </Card>
        <Card title="Gaps & risk">
          {!r.findings.length ? <Empty>No gaps.</Empty> : (
            <table className="table"><thead><tr><th>Priority</th><th>Gap</th><th>Para</th><th>Control</th></tr></thead>
              <tbody>{r.findings.map((f) => <tr key={f.id}><td><Badge value={f.priority} /> <span className="tabular-nums text-xs">{f.risk_score}</span></td><td><Badge value={f.gap_type} tone="red" /> {f.is_new ? <Badge value="NEW" tone="blue" /> : null}<div className="text-xs">{f.title}</div></td><td className="font-mono text-xs">{f.section_path}</td><td className="text-xs">{f.control_key ?? "—"}</td></tr>)}</tbody></table>
          )}
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Remediation recommended">
          {!r.remediation.length ? <Empty>None.</Empty> : r.remediation.map((a) => (
            <div key={a.id} className="border-b border-line py-2 text-sm last:border-0">
              <Badge value={a.priority} /> <Badge value={a.status} /> {a.requires_approval ? <Badge value="APPROVAL REQUIRED" tone="amber" /> : null}
              <p className="mt-1">{a.root_cause}</p><p className="text-xs text-ink-muted">{a.owner} · due {fmtDate(a.due_date)}</p>
            </div>
          ))}
        </Card>
        <Card title="Departments impacted">
          <div className="flex flex-wrap gap-2">{r.impacted_business_units.map((u) => <Badge key={u} value={u} tone="blue" />)}</div>
        </Card>
      </div>
    </>
  );
}
