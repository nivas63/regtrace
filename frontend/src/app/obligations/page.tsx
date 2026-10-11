"use client";

import { useState } from "react";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader, Score } from "@/components/ui";
import { useApi } from "@/lib/api";

type Row = { id: string; section_path: string; requirement_text: string; source_quote: string; obligation_type: string;
  modality: string; frequency: { period?: string | null; text?: string | null }; confidence: number; review_status: string;
  regulation_title: string; regulator: string; applicability: string | null; mapped_controls: number; open_gaps: number };

const TYPES = ["", "CUSTOMER_DUE_DILIGENCE", "RISK_ASSESSMENT", "RECORD_RETENTION", "REPORTING", "GOVERNANCE", "MONITORING",
  "TESTING_AUDIT", "TRAINING", "TECHNICAL_CONTROL", "DATA_PROTECTION", "BUSINESS_CONTINUITY", "INCIDENT_MANAGEMENT"];

export default function ObligationsPage() {
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (type) params.set("obligation_type", type);
  const { data, error, loading } = useApi<Row[]>(`/obligations?${params.toString()}`);
  return (
    <>
      <PageHeader title="Obligation explorer"
        subtitle="Atomic obligations extracted by the agent. Every one carries a verbatim, machine-verified source quote; low-confidence items are queued for human review." />
      <div className="mb-3 flex flex-wrap gap-2">
        <input className="input max-w-sm" placeholder="Search requirement text…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input max-w-xs" value={type} onChange={(e) => setType(e.target.value)}>
          {TYPES.map((t) => <option key={t} value={t}>{t ? t.replaceAll("_", " ") : "All types"}</option>)}
        </select>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty>No obligations match.</Empty> : (
        <Card>
          <table className="table">
            <thead><tr><th>Source</th><th>Obligation</th><th>Type</th><th>Period</th><th>Applicability</th><th>Controls</th><th>Gaps</th><th>Confidence</th><th>Review</th></tr></thead>
            <tbody>
              {data.map((o) => (
                <tr key={o.id}>
                  <td className="whitespace-nowrap text-xs"><div className="font-medium">{o.regulator}</div><div className="text-ink-muted">para {o.section_path}</div></td>
                  <td className="max-w-md"><A href={`/obligations/${o.id}`}>{o.requirement_text}</A></td>
                  <td><Badge value={o.obligation_type} tone="blue" /> <Badge value={o.modality} /></td>
                  <td className="font-mono text-xs">{o.frequency?.period ?? "—"}</td>
                  <td><Badge value={o.applicability} /></td>
                  <td className="tabular-nums">{o.mapped_controls}</td>
                  <td>{o.open_gaps ? <Badge value={String(o.open_gaps)} tone="red" /> : <span className="text-ink-muted">0</span>}</td>
                  <td><Score value={o.confidence} /></td>
                  <td><Badge value={o.review_status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
