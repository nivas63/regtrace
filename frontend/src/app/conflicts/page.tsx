"use client";

import { useState } from "react";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader, Quote, Tabs } from "@/components/ui";
import { fmtDate, post, useApi } from "@/lib/api";

type Rule = { id: string; section_path: string; source_quote: string; obligation_type: string; modality: string; effective_from: string | null;
  regulation: { title: string; regulator: string; ref_no: string } };
type Conflict = { id: string; conflict_type: string; signals: string[]; reason: string; resolution_hint: string | null;
  applicability_overlap: string | null; confidence: number; review_status: string; label: string; rule_a: Rule; rule_b: Rule };
type Cluster = { id: string; topic: string; summary: string; confirmed_by: string; confidence: number; shared_control_suggestion: string | null;
  members: (Rule & { similarity: number })[] };

function RuleView({ label, rule }: { label: string; rule: Rule }) {
  return (
    <div>
      <div className="label">{label}</div>
      <Quote source={<>{rule.regulation.regulator} · {rule.regulation.ref_no} · <A href={`/obligations/${rule.id}`}>para {rule.section_path}</A> · effective {fmtDate(rule.effective_from)}</>}>{rule.source_quote}</Quote>
    </div>
  );
}

export default function ConflictsPage() {
  const [tab, setTab] = useState("Potential conflicts");
  const [message, setMessage] = useState<string | null>(null);
  const conflicts = useApi<Conflict[]>("/conflicts", 15000);
  const clusters = useApi<Cluster[]>("/clusters", 15000);
  async function detect() {
    const res = await post<{ run_id: string }>("/contradictions/detect");
    setMessage(`Cross-regulation analysis queued (run ${res.run_id.slice(0, 8)})`);
  }
  return (
    <>
      <PageHeader title="Cross-regulation intelligence"
        subtitle="Potential conflicts are never declared as contradictions: deterministic pre-checks (retention polarity, duration mismatch, modality opposition) select candidates, the model explains, and a human decides."
        actions={<button className="btn-primary" onClick={detect}>Re-run analysis</button>} />
      {message ? <p className="mb-2 text-sm text-ink-soft">{message}</p> : null}
      <Tabs tabs={["Potential conflicts", "Equivalent obligations"]} active={tab} onChange={setTab} />
      <ErrorBox error={conflicts.error ?? clusters.error} />
      {tab === "Potential conflicts" ? (
        conflicts.loading && !conflicts.data ? <Loading /> : !conflicts.data?.length ? <Empty>No potential conflicts detected.</Empty> : (
          <div className="space-y-3">
            {conflicts.data.map((c) => (
              <Card key={c.id} title={<span>{c.label} <Badge value={c.conflict_type} tone="red" /></span>}
                actions={<span className="flex items-center gap-2 text-xs">confidence {c.confidence.toFixed(2)} <Badge value={c.review_status} /></span>}>
                <div className="grid gap-3 md:grid-cols-2"><RuleView label="Rule A" rule={c.rule_a} /><RuleView label="Rule B" rule={c.rule_b} /></div>
                <p className="mt-3 text-sm"><span className="font-semibold">Reason:</span> {c.reason}</p>
                {c.resolution_hint ? <p className="mt-1 rounded bg-emerald-50 p-2 text-sm text-emerald-800"><span className="font-semibold">Possible reconciliation:</span> {c.resolution_hint}</p> : null}
                {c.applicability_overlap ? <p className="mt-1 text-xs text-ink-muted">Applicability overlap: {c.applicability_overlap}</p> : null}
                <div className="mt-2 flex flex-wrap gap-1">{c.signals.map((s) => <Badge key={s} value={s.split(":")[0]} tone="gray" />)}</div>
              </Card>
            ))}
          </div>
        )
      ) : clusters.loading && !clusters.data ? <Loading /> : !clusters.data?.length ? <Empty>No equivalent obligations clustered.</Empty> : (
        <div className="space-y-3">
          {clusters.data.map((cl) => (
            <Card key={cl.id} title={<span>{cl.topic} <Badge value={cl.confirmed_by} tone="gray" /></span>}>
              <p className="text-sm">{cl.summary}</p>
              {cl.shared_control_suggestion ? <p className="mt-1 rounded bg-indigo-50 p-2 text-sm text-indigo-800">Shared control opportunity: {cl.shared_control_suggestion}</p> : null}
              <div className="mt-3 grid gap-3 md:grid-cols-2">{cl.members.map((m) => <RuleView key={m.id} label={`similarity ${m.similarity}`} rule={m} />)}</div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
