"use client";

import { Badge, Card, Empty, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { api, fmtDate, useApi } from "@/lib/api";

type Plan = { root_cause: string; actions: { kind: string; description: string }[]; suggested_owner: string; target_days: number;
  dependencies: string[]; expected_evidence: string; verification_method: string; touches_production: boolean };
type Action = { id: string; priority: string; status: string; owner: string | null; due_date: string | null; requires_approval: boolean;
  plan: Plan; finding: { gap_type: string; title: string }; section_path: string; regulation_title: string };

export default function RemediationPage() {
  const { data, error, loading, reload } = useApi<Action[]>("/remediation", 15000);
  async function advance(id: string, status: "IN_PROGRESS" | "DONE") {
    try {
      await api(`/remediation/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
      await reload();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <>
      <PageHeader title="Remediation tracker"
        subtitle="Plans are AI recommendations. Due dates, priority and the approval requirement are set by code; P1/P2 plans and anything touching production need an approver before execution." />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty>No remediation proposed yet.</Empty> : (
        <div className="grid gap-3 lg:grid-cols-2">
          {data.map((a) => (
            <Card key={a.id} title={<span><Badge value={a.priority} /> {a.finding.title}</span>}
              actions={<span className="flex items-center gap-2"><Badge value={a.status} />{a.requires_approval ? <Badge value="APPROVAL REQUIRED" tone="amber" /> : null}</span>}>
              <p className="text-xs text-ink-muted">{a.regulation_title} · para {a.section_path} · {a.finding.gap_type.replaceAll("_", " ").toLowerCase()}</p>
              <p className="mt-2 text-sm"><span className="font-semibold">Root cause:</span> {a.plan.root_cause}</p>
              <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
                {a.plan.actions.map((s, i) => <li key={i}><Badge value={s.kind} tone="gray" /> {s.description}</li>)}
              </ol>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div><div className="label">Owner</div>{a.owner}</div>
                <div><div className="label">Due</div>{fmtDate(a.due_date)}</div>
                <div><div className="label">Expected evidence</div>{a.plan.expected_evidence}</div>
                <div><div className="label">Verification</div>{a.plan.verification_method}</div>
              </div>
              <div className="mt-3 flex gap-2">
                <button className="btn-ghost" disabled={a.requires_approval && a.status === "PROPOSED" || a.status === "DONE" || a.status === "REJECTED"} onClick={() => advance(a.id, "IN_PROGRESS")}>Start</button>
                <button className="btn-ghost" disabled={a.status !== "IN_PROGRESS"} onClick={() => advance(a.id, "DONE")}>Mark done</button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
