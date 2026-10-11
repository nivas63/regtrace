"use client";

import { useState } from "react";

import { Badge, Card, Empty, ErrorBox, Json, Loading, PageHeader, Score } from "@/components/ui";
import { fmtDateTime, getUser, post, useApi } from "@/lib/api";

type Review = { id: string; subject_type: string; subject_id: string; reason: string; recommendation: Record<string, unknown>; confidence: number | null;
  priority: string; status: string; comment: string | null; created_at: string; decided_at: string | null };

const TYPES = ["", "OBLIGATION", "MAPPING", "APPLICABILITY", "CONFLICT", "REMEDIATION", "AMENDMENT", "CONTROL", "IMPACT_REPORT", "RUN_FAILURE"];

export default function ReviewsPage() {
  const [status, setStatus] = useState("PENDING");
  const [type, setType] = useState("");
  const [comments, setComments] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const canDecide = ["reviewer", "admin"].includes(getUser()?.role ?? "");
  const { data, loading, reload } = useApi<Review[]>(`/reviews?status=${status}${type ? `&subject_type=${type}` : ""}`, 10000);
  async function decide(id: string, decision: "APPROVE" | "REJECT") {
    setError(null);
    try {
      await post(`/reviews/${id}/decision`, { decision, comment: comments[id] || null });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <>
      <PageHeader title="Human review queue"
        subtitle="Decisions the system will not make alone: low-confidence obligations and mappings, LLM-decided applicability, every potential conflict, P1/P2 or production-touching remediation, amendment consolidation. Every decision is audit-logged; segregation of duties is enforced."
        actions={<>
          <select className="input w-40" value={status} onChange={(e) => setStatus(e.target.value)}>{["PENDING", "APPROVED", "REJECTED", "ALL"].map((s) => <option key={s}>{s}</option>)}</select>
          <select className="input w-44" value={type} onChange={(e) => setType(e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{t || "All types"}</option>)}</select>
        </>} />
      {!canDecide ? <p className="mb-3 rounded bg-slate-100 p-2 text-xs text-ink-soft">Your role can view the queue but only reviewers can decide.</p> : null}
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty>Nothing to review.</Empty> : (
        <div className="space-y-3">
          {data.map((r) => (
            <Card key={r.id} title={<span className="flex items-center gap-2"><Badge value={r.subject_type} tone="blue" /> <Badge value={r.priority} /> {r.reason}</span>}
              actions={<Badge value={r.status} />}>
              <div className="flex flex-wrap gap-4 text-xs text-ink-muted">
                <span>Created {fmtDateTime(r.created_at)}</span>{r.confidence !== null ? <span className="flex items-center gap-1">Agent confidence <Score value={r.confidence} /></span> : null}
              </div>
              {Object.keys(r.recommendation).length ? <details className="mt-2 text-xs"><summary className="cursor-pointer text-ink-muted">Agent recommendation & evidence</summary><Json value={r.recommendation} /></details> : null}
              {r.status === "PENDING" && canDecide ? (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <input className="input max-w-md" placeholder="Comment (recorded in the audit log)" value={comments[r.id] ?? ""} onChange={(e) => setComments({ ...comments, [r.id]: e.target.value })} />
                  <button className="btn-primary" onClick={() => decide(r.id, "APPROVE")}>Approve</button>
                  <button className="btn-ghost" onClick={() => decide(r.id, "REJECT")}>Reject</button>
                </div>
              ) : r.comment ? <p className="mt-2 text-xs">Comment: {r.comment}</p> : null}
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
