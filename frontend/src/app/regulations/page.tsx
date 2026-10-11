"use client";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { fmtDate, useApi } from "@/lib/api";

type Version = { id: string; version_no: number; effective_from: string | null; effective_to: string | null; status: string; origin: string };
type Regulation = { id: string; title: string; regulator: string; ref_no: string | null; is_synthetic: boolean;
  versions: Version[]; active_obligations: number; material_changes: number };

export default function RegulationsPage() {
  const { data, error, loading } = useApi<Regulation[]>("/regulations", 15000);
  return (
    <>
      <PageHeader title="Regulatory intelligence"
        subtitle="Regulations ingested with their version history. Each version has an effective window; retrieval and assessment use the version in force." />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty>No regulations yet. Upload one under Documents.</Empty> : (
        <Card>
          <table className="table">
            <thead><tr><th>Regulation</th><th>Regulator</th><th>Versions</th><th>In force</th><th>Obligations</th><th>Material changes</th></tr></thead>
            <tbody>
              {data.map((r) => {
                const current = [...r.versions].reverse().find((v) => v.status === "ACTIVE") ?? r.versions.at(-1);
                return (
                  <tr key={r.id}>
                    <td>
                      <A href={`/regulations/${r.id}`}>{r.title}</A>
                      <div className="text-xs text-ink-muted">{r.ref_no} {r.is_synthetic ? <Badge value="SYNTHETIC" tone="amber" /> : null}</div>
                    </td>
                    <td>{r.regulator}</td>
                    <td className="space-x-1">{r.versions.map((v) => <Badge key={v.id} value={`v${v.version_no} ${v.status}`} tone={v.status === "ACTIVE" ? "green" : "gray"} />)}</td>
                    <td className="text-ink-soft">v{current?.version_no} from {fmtDate(current?.effective_from)}</td>
                    <td className="tabular-nums">{r.active_obligations}</td>
                    <td className="tabular-nums">{r.material_changes}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
