"use client";

import { useParams } from "next/navigation";

import { A, Badge, Card, Empty, ErrorBox, Json, Loading, PageHeader, Quote, Score } from "@/components/ui";
import { fmtDate, useApi } from "@/lib/api";

type Assessment = { id: string; obligation_id: string | null; design_score: number; operating_score: number | null; evidence_score: number;
  recency_score: number; score: number; rating: string; factors: { reasons?: string[]; required_period?: string; last_activity?: string } & Record<string, unknown>; formula_version: string };
type Detail = {
  id: string; control_key: string; name: string; description: string; frequency: string | null; owner: string | null;
  business_unit: string | null; systems: string[]; procedure_ref: string | null; control_type: string; automation: string;
  assessments: Assessment[];
  tests: { id: string; tested_on: string; result: string; sample_size: number; exceptions: number; notes: string | null }[];
  evidence: { id: string; title: string; evidence_type: string; collected_on: string | null; authenticity: string; document_id: string | null }[];
  evidence_judgements: { id: string; criterion: string; verdict: string; quote: string | null; quote_verified: boolean; rationale: string; created_at: string }[];
  mappings: { id: string; mapping_type: string; coverage_score: number; review_status: string; regulation_title: string;
    obligation: { id: string; section_path: string; requirement_text: string; frequency: { period?: string } } }[];
};

export default function ControlDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading } = useApi<Detail>(`/controls/${id}`);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} />;
  const latestRun = data.evidence_judgements[0]?.created_at?.slice(0, 16);
  const latest = data.evidence_judgements.filter((j) => j.created_at.slice(0, 16) === latestRun);
  return (
    <>
      <PageHeader title={`${data.control_key} · ${data.name}`}
        subtitle={<>{data.control_type.toLowerCase()} · {data.automation.toLowerCase()} · runs {data.frequency ?? "unspecified"} · owner {data.owner ?? "—"} ({data.business_unit ?? "—"})</>} />
      <Card title="Description"><p className="text-sm">{data.description}</p>
        <p className="mt-2 text-xs text-ink-muted">Systems: {data.systems.join(", ") || "—"} · Procedure: {data.procedure_ref ?? "—"}</p></Card>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Effectiveness per mapped obligation (deterministic)">
          {!data.assessments.length ? <Empty>Not assessed yet.</Empty> : data.assessments.map((a) => (
            <div key={a.id} className="mb-3 border-b border-line pb-3 last:border-0">
              <div className="mb-2 flex items-center gap-2"><Badge value={a.rating} /> <Score value={a.score} />
                <span className="text-xs text-ink-muted">required {a.factors.required_period ?? "n/a"} · last activity {fmtDate(a.factors.last_activity as string)}</span></div>
              <div className="grid grid-cols-4 gap-2 text-xs">
                <div>Design<Score value={a.design_score} /></div><div>Operating<Score value={a.operating_score} /></div>
                <div>Evidence<Score value={a.evidence_score} /></div><div>Recency<Score value={a.recency_score} /></div>
              </div>
              {a.factors.reasons?.length ? <ul className="mt-2 list-disc pl-5 text-xs text-ink-soft">{a.factors.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul> : null}
              <details className="mt-1 text-xs"><summary className="cursor-pointer text-ink-muted">Factor vector (formula {a.formula_version})</summary><Json value={a.factors} /></details>
            </div>
          ))}
        </Card>
        <Card title="Mapped obligations">
          {data.mappings.map((m) => (
            <div key={m.id} className="border-b border-line py-2 text-sm last:border-0">
              <A href={`/obligations/${m.obligation.id}`}>{m.regulation_title} · {m.obligation.section_path}</A> <Badge value={m.mapping_type} /> <Badge value={m.review_status} />
              <p className="text-xs text-ink-soft">{m.obligation.requirement_text} <span className="font-mono">({m.obligation.frequency?.period ?? "—"})</span></p>
            </div>
          ))}
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Control tests">
          {!data.tests.length ? <Empty>No test on record — operating effectiveness unknown.</Empty> : (
            <table className="table"><thead><tr><th>Date</th><th>Result</th><th>Sample</th><th>Exceptions</th><th>Notes</th></tr></thead>
              <tbody>{data.tests.map((t) => <tr key={t.id}><td>{fmtDate(t.tested_on)}</td><td><Badge value={t.result} tone={t.result === "PASS" ? "green" : t.result === "FAIL" ? "red" : "amber"} /></td><td>{t.sample_size}</td><td>{t.exceptions}</td><td className="text-xs">{t.notes}</td></tr>)}</tbody></table>
          )}
        </Card>
        <Card title="Evidence assessment (latest run)">
          {!data.evidence.length ? <Empty>No evidence artefact linked. The control cannot be rated Effective.</Empty> : null}
          {data.evidence.map((e) => <div key={e.id} className="text-sm">{e.title} <Badge value={e.evidence_type} tone="gray" /> <span className="text-xs text-ink-muted">{fmtDate(e.collected_on)} · {e.authenticity.toLowerCase()}</span></div>)}
          <div className="mt-3 space-y-2">
            {latest.map((j) => (
              <div key={j.id} className="rounded-lg border border-line p-2 text-sm">
                <div className="flex items-center gap-2"><Badge value={j.verdict} /> <span className="font-medium">{j.criterion}</span></div>
                {j.quote ? <Quote source={j.quote_verified ? "quote verified against the evidence text" : "unverified"}>{j.quote}</Quote> : null}
                <p className="text-xs text-ink-muted">{j.rationale}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
