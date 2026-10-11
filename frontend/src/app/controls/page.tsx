"use client";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader, Score } from "@/components/ui";
import { useApi } from "@/lib/api";

type Control = { id: string; control_key: string; name: string; control_type: string; automation: string; frequency: string | null;
  owner: string | null; business_unit: string | null; mapped_obligations: number; evidence_items: number;
  assessment: { score: number; rating: string; design_score: number; operating_score: number | null; evidence_score: number; recency_score: number } | null };

export default function ControlsPage() {
  const { data, error, loading } = useApi<Control[]>("/controls", 15000);
  return (
    <>
      <PageHeader title="Control framework"
        subtitle="Controls imported from the control matrix and reconciled with policy documents. Scores are deterministic: design × operating × evidence × recency (weakest mapped obligation shown)." />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty>Upload a control matrix (XLSX) under Documents.</Empty> : (
        <Card>
          <table className="table">
            <thead><tr><th>Control</th><th>Type</th><th>Frequency</th><th>Owner</th><th>Obligations</th><th>Evidence</th><th>Design</th><th>Operating</th><th>Evidence</th><th>Recency</th><th>Score</th><th>Rating</th></tr></thead>
            <tbody>
              {data.map((c) => (
                <tr key={c.id}>
                  <td><A href={`/controls/${c.id}`}>{c.control_key}</A><div className="text-xs text-ink-muted">{c.name}</div></td>
                  <td className="text-xs">{c.control_type.toLowerCase()}<div className="text-ink-muted">{c.automation.toLowerCase().replace("_", "-")}</div></td>
                  <td className="font-mono text-xs">{c.frequency ?? "—"}</td>
                  <td className="text-xs">{c.owner}<div className="text-ink-muted">{c.business_unit}</div></td>
                  <td className="tabular-nums">{c.mapped_obligations}</td>
                  <td className="tabular-nums">{c.evidence_items}</td>
                  <td><Score value={c.assessment?.design_score} /></td>
                  <td><Score value={c.assessment?.operating_score} /></td>
                  <td><Score value={c.assessment?.evidence_score} /></td>
                  <td><Score value={c.assessment?.recency_score} /></td>
                  <td><Score value={c.assessment?.score} /></td>
                  <td><Badge value={c.assessment?.rating} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
