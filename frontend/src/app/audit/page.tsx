"use client";

import { Badge, Card, Empty, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { fmtDateTime, useApi } from "@/lib/api";

type Entry = { id: number; ts: string; actor_type: string; actor_id: string; action: string; subject_type: string | null; subject_id: string | null;
  details: Record<string, unknown>; hash: string; prev_hash: string };
type Verify = { bank_chain: { valid: boolean; entries: number; first_broken_id: number | null }; system_chain: { valid: boolean; entries: number } };

export default function AuditPage() {
  const log = useApi<Entry[]>("/audit?limit=300", 15000);
  const verify = useApi<Verify>("/audit/verify");
  return (
    <>
      <PageHeader title="Audit log" subtitle="Append-only and hash-chained: each entry hashes the previous one. The application role cannot update or delete entries; verification detects any tampering." />
      {verify.data ? (
        <div className={`mb-4 rounded-lg px-3 py-2 text-sm ${verify.data.bank_chain.valid ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-700"}`}>
          Chain integrity: {verify.data.bank_chain.valid ? "VALID" : `BROKEN at entry ${verify.data.bank_chain.first_broken_id}`} · {verify.data.bank_chain.entries} bank entries verified
        </div>
      ) : null}
      <ErrorBox error={log.error ?? verify.error} />
      {log.loading && !log.data ? <Loading /> : !log.data?.length ? <Empty>No entries (auditor or reviewer role required).</Empty> : (
        <Card>
          <table className="table">
            <thead><tr><th>#</th><th>Time</th><th>Actor</th><th>Action</th><th>Subject</th><th>Details</th><th>Hash</th></tr></thead>
            <tbody>
              {log.data.map((e) => (
                <tr key={e.id}>
                  <td className="tabular-nums text-xs">{e.id}</td>
                  <td className="whitespace-nowrap text-xs">{fmtDateTime(e.ts)}</td>
                  <td className="text-xs"><Badge value={e.actor_type} tone="gray" /> {e.actor_type === "USER" ? e.actor_id.slice(0, 8) : e.actor_id}</td>
                  <td className="text-xs font-medium">{e.action}</td>
                  <td className="text-xs text-ink-muted">{e.subject_type} {e.subject_id?.slice(0, 8)}</td>
                  <td className="max-w-sm truncate text-[11px] text-ink-muted" title={JSON.stringify(e.details)}>{JSON.stringify(e.details)}</td>
                  <td className="font-mono text-[10px] text-ink-muted">{e.hash.slice(0, 10)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
