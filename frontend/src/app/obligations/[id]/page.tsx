"use client";

import { useParams } from "next/navigation";

import { A, Badge, Card, Empty, ErrorBox, Highlight, Json, Loading, PageHeader, Score } from "@/components/ui";
import { fmtDate, useApi } from "@/lib/api";

type Detail = {
  id: string; requirement_text: string; source_quote: string; char_start: number; char_end: number; modality: string;
  obligation_type: string; subject: string; action: string; condition: string | null; frequency: Record<string, unknown>;
  deadline_text: string | null; responsible_function: string | null; required_evidence: { description: string }[];
  ambiguity_note: string | null; confidence: number; confidence_factors: Record<string, unknown>; review_status: string;
  status: string; obligation_key: string; prompt_version: string; model: string; effective_from: string | null;
  section: { text: string; section_path: string; title: string | null; page_from: number } | null;
  regulation: { id: string; title: string; regulator: string } | null;
  version: { version_no: number; effective_from: string | null } | null;
  mappings: { id: string; mapping_type: string; coverage_score: number; confidence: number; reason: string; control_quote: string | null;
    review_status: string; control: { id: string; control_key: string; name: string; frequency: string; owner: string } }[];
  applicability_decision: { decision: string; decided_by: string; rationale: string; confidence: number } | null;
  lineage: { prev_obligation_id: string | null; next_obligation_id: string | null; change_type: string; similarity: number | null }[];
  findings: { id: string; gap_type: string; status: string; title: string }[];
};

export default function ObligationDetail() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading } = useApi<Detail>(`/obligations/${id}`);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} />;
  return (
    <>
      <PageHeader title={data.requirement_text}
        subtitle={<>{data.regulation?.regulator} · <A href={`/regulations/${data.regulation?.id}`}>{data.regulation?.title}</A> · para {data.section?.section_path} · version {data.version?.version_no} (from {fmtDate(data.version?.effective_from)})</>} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Source text (verified quote highlighted)" className="lg:col-span-2">
          {data.section ? <p className="text-sm leading-relaxed"><Highlight text={data.section.text} start={data.char_start} end={data.char_end} /></p> : null}
          <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
            <div><div className="label">Modality</div><Badge value={data.modality} /></div>
            <div><div className="label">Type</div><Badge value={data.obligation_type} tone="blue" /></div>
            <div><div className="label">Frequency</div><span className="font-mono text-xs">{String(data.frequency?.period ?? "—")}</span><div className="text-xs text-ink-muted">{String(data.frequency?.text ?? "")}</div></div>
            <div><div className="label">Deadline</div>{data.deadline_text ?? "—"}</div>
            <div><div className="label">Subject</div>{data.subject}</div>
            <div className="col-span-2"><div className="label">Action</div>{data.action}</div>
            <div><div className="label">Responsible</div>{data.responsible_function ?? "—"}</div>
          </div>
          {data.ambiguity_note ? <p className="mt-3 rounded bg-amber-50 p-2 text-sm text-amber-800">Ambiguity: {data.ambiguity_note}</p> : null}
        </Card>
        <Card title="Confidence & provenance">
          <div className="mb-2"><Score value={data.confidence} /></div>
          <Json value={data.confidence_factors} />
          <div className="mt-2 space-y-1 text-xs text-ink-muted">
            <div>Lineage key: <span className="font-mono">{data.obligation_key}</span></div>
            <div>Prompt {data.prompt_version ?? "—"} · model {data.model ?? "—"}</div>
            <div>Status <Badge value={data.status} /> · review <Badge value={data.review_status} /></div>
          </div>
        </Card>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card title="Mapped controls">
          {!data.mappings.length ? <Empty>No control mapped — a MISSING_CONTROL gap is raised.</Empty> : data.mappings.map((m) => (
            <div key={m.id} className="border-b border-line py-2 last:border-0">
              <div className="flex items-center gap-2 text-sm">
                <A href={`/controls/${m.control.id}`}>{m.control.control_key}</A> {m.control.name}
                <Badge value={m.mapping_type} /> <Badge value={m.review_status} />
                <span className="ml-auto text-xs text-ink-muted">coverage {Math.round(m.coverage_score * 100)}% · conf {m.confidence.toFixed(2)}</span>
              </div>
              <p className="mt-1 text-xs text-ink-soft">{m.reason}</p>
              {m.control_quote ? <p className="quote mt-1 text-xs">{m.control_quote}</p> : null}
            </div>
          ))}
        </Card>
        <div className="space-y-4">
          <Card title="Applicability">
            {data.applicability_decision ? (
              <p className="text-sm"><Badge value={data.applicability_decision.decision} /> by {data.applicability_decision.decided_by} — {data.applicability_decision.rationale}</p>
            ) : <Empty>Not assessed.</Empty>}
          </Card>
          <Card title="Required evidence">
            <ul className="list-disc pl-5 text-sm">{data.required_evidence.map((e, i) => <li key={i}>{e.description}</li>)}</ul>
          </Card>
          <Card title="Findings & lineage">
            {data.findings.map((f) => <div key={f.id} className="text-sm"><Badge value={f.gap_type} tone="red" /> <Badge value={f.status} /> {f.title}</div>)}
            {data.lineage.map((l, i) => (
              <div key={i} className="mt-1 text-xs text-ink-muted">
                Lineage <Badge value={l.change_type} /> {l.prev_obligation_id && l.prev_obligation_id !== data.id ? <A href={`/obligations/${l.prev_obligation_id}`}>previous version</A> : null}
                {l.next_obligation_id && l.next_obligation_id !== data.id ? <A href={`/obligations/${l.next_obligation_id}`}>next version</A> : null}
                {l.similarity !== null ? ` (similarity ${l.similarity})` : ""}
              </div>
            ))}
            {!data.findings.length && !data.lineage.length ? <Empty>No findings.</Empty> : null}
          </Card>
        </div>
      </div>
    </>
  );
}
