"use client";

import { A, Card, Empty, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { fmtDateTime, useApi } from "@/lib/api";

type Impact = { id: string; run_id: string; summary: string; created_at: string; to_version_id: string | null; from_version_id: string | null };

export default function ImpactList() {
  const { data, error, loading } = useApi<Impact[]>("/impact", 10000);
  return (
    <>
      <PageHeader title="Regulatory change impact"
        subtitle="Generated autonomously after every upload: the supervisor plans the analysis, agents run it, and the report traces changes to obligations, controls, evidence, gaps, risk, remediation and departments." />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty>No impact analyses yet.</Empty> : (
        <div className="space-y-3">
          {data.map((i) => (
            <Card key={i.id}>
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <A href={`/impact/${i.id}`}>{i.from_version_id ? "Version change impact" : "New document impact"} · {fmtDateTime(i.created_at)}</A>
                <A href={`/runs/${i.run_id}`}>View agent trace →</A>
              </div>
              <p className="mt-1 text-sm text-ink-soft">{i.summary}</p>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
