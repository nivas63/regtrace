"use client";

import { useParams } from "next/navigation";
import { useState } from "react";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader, Tabs } from "@/components/ui";
import { fmtDate, useApi } from "@/lib/api";

type SectionObligation = { id: string; requirement_text: string; char_start: number; char_end: number; obligation_type: string; modality: string; confidence: number; status: string };
type Section = { id: string; section_path: string; title: string | null; section_type: string; text: string; page_from: number; obligations: SectionObligation[] };
type Version = { id: string; version_no: number; effective_from: string | null; effective_to: string | null; status: string; origin: string };
type Detail = { id: string; title: string; regulator: string; ref_no: string; as_of: string; versions: Version[];
  version_in_force: Version | null; sections: Section[]; document: { id: string; filename: string } | null };
type Change = { id: string; section_path: string; change_type: string; semantic_category: string; is_material: boolean;
  summary: string; old_text: string | null; new_text: string | null; diff: { op: string; old: string; new: string }[]; confidence: number };

function SectionText({ section }: { section: Section }) {
  const spans = [...section.obligations].filter((o) => o.char_end > o.char_start).sort((a, b) => a.char_start - b.char_start);
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  spans.forEach((o, i) => {
    if (o.char_start < cursor) return;
    parts.push(section.text.slice(cursor, o.char_start));
    parts.push(<mark key={i} title={`${o.obligation_type} · ${o.modality} · confidence ${o.confidence.toFixed(2)}`} className="rounded bg-yellow-100 px-0.5">
      <A href={`/obligations/${o.id}`}>{section.text.slice(o.char_start, o.char_end)}</A></mark>);
    cursor = o.char_end;
  });
  parts.push(section.text.slice(cursor));
  return <p className="whitespace-pre-wrap text-sm leading-relaxed">{parts}</p>;
}

export default function RegulationDetail() {
  const { id } = useParams<{ id: string }>();
  const [asOf, setAsOf] = useState<string>("");
  const [tab, setTab] = useState("Text & obligations");
  const { data, error, loading } = useApi<Detail>(`/regulations/${id}${asOf ? `?as_of=${asOf}` : ""}`);
  const changes = useApi<Change[]>(`/regulations/${id}/changes`);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} />;
  return (
    <>
      <PageHeader title={data.title} subtitle={<>{data.regulator} · {data.ref_no} · showing version in force on {fmtDate(data.as_of)}</>}
        actions={<input type="date" className="input w-44" value={asOf} onChange={(e) => setAsOf(e.target.value)} title="Temporal view: regulation as of a date" />} />
      <div className="mb-4 flex flex-wrap gap-2">
        {data.versions.map((v) => (
          <div key={v.id} className={`card px-3 py-2 text-xs ${v.id === data.version_in_force?.id ? "ring-2 ring-accent" : ""}`}>
            <div className="font-semibold">Version {v.version_no} <Badge value={v.status} /></div>
            <div className="text-ink-muted">{fmtDate(v.effective_from)} → {v.effective_to ? fmtDate(v.effective_to) : "in force"} · {v.origin.replaceAll("_", " ").toLowerCase()}</div>
          </div>
        ))}
      </div>
      <Tabs tabs={["Text & obligations", `Changes (${changes.data?.length ?? 0})`]} active={tab.startsWith("Changes") ? `Changes (${changes.data?.length ?? 0})` : tab} onChange={setTab} />
      {tab.startsWith("Changes") ? (
        !changes.data?.length ? <Empty>No version-to-version changes recorded.</Empty> : (
          <div className="space-y-3">
            {changes.data.map((c) => (
              <Card key={c.id} title={<span>Para {c.section_path} <Badge value={c.change_type} /> <Badge value={c.semantic_category} /> {c.is_material ? <Badge value="MATERIAL" tone="red" /> : <Badge value="EDITORIAL / NON-MATERIAL" tone="gray" />}</span>}>
                <p className="mb-3 text-sm">{c.summary} <span className="text-xs text-ink-muted">(confidence {c.confidence?.toFixed(2)})</span></p>
                <div className="grid gap-3 md:grid-cols-2">
                  <div><div className="label">Previous</div><p className="rounded bg-rose-50 p-2 text-sm">{c.old_text ?? "—"}</p></div>
                  <div><div className="label">New</div><p className="rounded bg-emerald-50 p-2 text-sm">{c.new_text ?? "—"}</p></div>
                </div>
                {c.diff.length ? <div className="mt-2 text-xs text-ink-muted">Word diff: {c.diff.map((d, i) => <span key={i} className="mr-2"><del className="text-rose-600">{d.old}</del> → <ins className="text-emerald-700">{d.new}</ins></span>)}</div> : null}
              </Card>
            ))}
          </div>
        )
      ) : (
        <Card>
          {data.sections.map((s) => (
            <div key={s.id} className={`border-b border-line py-3 last:border-0 ${s.section_type === "CHAPTER" ? "pt-5" : ""}`}>
              <div className="mb-1 flex items-center gap-2">
                <span className={`font-mono text-xs ${s.section_type === "CHAPTER" ? "font-bold text-ink" : "text-ink-muted"}`}>{s.section_path}</span>
                {s.title ? <span className="text-sm font-semibold">{s.title}</span> : null}
                {s.obligations.length ? <Badge value={`${s.obligations.length} obligation${s.obligations.length > 1 ? "s" : ""}`} tone="blue" /> : null}
                <span className="ml-auto text-[11px] text-ink-muted">p.{s.page_from}</span>
              </div>
              {s.text ? <SectionText section={s} /> : null}
            </div>
          ))}
        </Card>
      )}
    </>
  );
}
