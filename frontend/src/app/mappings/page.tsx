"use client";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader, Score } from "@/components/ui";
import { useApi } from "@/lib/api";

type Mapping = { id: string; mapping_type: string; coverage_score: number; confidence: number; reason: string; review_status: string;
  proposed_by: string; control_quote: string | null; evidence: { rule_reasons?: string[]; quote_verified?: boolean };
  obligation: { id: string; section_path: string; requirement_text: string; obligation_type: string }; regulation_title: string;
  control: { id: string; control_key: string; name: string } };

export default function MappingsPage() {
  const { data, error, loading } = useApi<{ mappings: Mapping[] }>("/mappings", 20000);
  const rows = data?.mappings ?? [];
  return (
    <>
      <PageHeader title="Regulation → control mapping"
        subtitle="Many-to-many links proposed by retrieval + rules + LLM adjudication, with a machine-verified control quote. Low-confidence proposals go to the review queue; human decisions are never overwritten." />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !rows.length ? <Empty>No mappings yet.</Empty> : (
        <Card>
          <table className="table">
            <thead><tr><th>Obligation</th><th>Control</th><th>Relation</th><th>Coverage</th><th>Confidence</th><th>Why</th><th>Status</th></tr></thead>
            <tbody>
              {rows.map((m) => (
                <tr key={m.id}>
                  <td className="max-w-xs text-xs"><A href={`/obligations/${m.obligation.id}`}>{m.regulation_title} · {m.obligation.section_path}</A><div className="text-ink-soft">{m.obligation.requirement_text}</div></td>
                  <td className="text-xs"><A href={`/controls/${m.control.id}`}>{m.control.control_key}</A><div className="text-ink-muted">{m.control.name}</div></td>
                  <td><Badge value={m.mapping_type} /></td>
                  <td><Score value={m.coverage_score} /></td>
                  <td><Score value={m.confidence} /><div className="text-[10px] text-ink-muted">{m.evidence.quote_verified ? "quote verified" : "no verified quote"}</div></td>
                  <td className="max-w-sm text-xs text-ink-soft">{m.reason}</td>
                  <td><Badge value={m.review_status} /><div className="text-[10px] text-ink-muted">{m.proposed_by.toLowerCase()}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
