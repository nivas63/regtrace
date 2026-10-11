"use client";

import { useState, type FormEvent } from "react";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader } from "@/components/ui";
import { api, fmtDateTime, useApi } from "@/lib/api";

type Doc = { id: string; filename: string; doc_class: string; status: string; title: string | null; page_count: number | null; is_synthetic: boolean;
  created_at: string; parse_report: { paragraphs?: number; chunks?: number; quarantined_chunks?: number }; run_id: string | null; run_status: string | null };

const CLASSES = ["REGULATION", "AMENDMENT", "POLICY", "CONTROL_MATRIX", "CONTROL_TESTS", "EVIDENCE"];

export default function DocumentsPage() {
  const { data, error, loading, reload } = useApi<Doc[]>("/documents", 4000);
  const [file, setFile] = useState<File | null>(null);
  const [docClass, setDocClass] = useState("REGULATION");
  const [controlKey, setControlKey] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setUploadError(null);
    const form = new FormData();
    form.append("file", file);
    form.append("doc_class", docClass);
    if (docClass === "EVIDENCE" && controlKey) form.append("attributes", JSON.stringify({ control_key: controlKey }));
    try {
      const res = await api<{ duplicate: boolean; run_id: string | null; document: { id: string } }>("/documents/upload", {
        method: "POST", body: form, headers: { "Idempotency-Key": `${file.name}-${file.size}-${file.lastModified}` },
      });
      setMessage(res.duplicate ? "Identical file already ingested (content-addressed) — no reprocessing." : `Uploaded. Autonomous processing started (run ${res.run_id?.slice(0, 8)}).`);
      await reload();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <PageHeader title="Documents & upload"
        subtitle="Uploads are validated (magic bytes, size, encryption, active PDF content, zip bombs), stored content-addressed, then processed by the agent workflow. Uploading a new regulation version triggers autonomous impact analysis." />
      <form onSubmit={upload} className="card mb-4 flex flex-wrap items-end gap-3 p-4">
        <div><label className="label">File</label><input type="file" className="text-sm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} accept=".pdf,.docx,.xlsx,.html,.htm,.txt,.md,.csv" /></div>
        <div><label className="label">Class</label><select className="input" value={docClass} onChange={(e) => setDocClass(e.target.value)}>{CLASSES.map((c) => <option key={c}>{c}</option>)}</select></div>
        {docClass === "EVIDENCE" ? <div><label className="label">Control key</label><input className="input w-36" value={controlKey} onChange={(e) => setControlKey(e.target.value)} placeholder="C-IT-01" /></div> : null}
        <button className="btn-primary" disabled={!file}>Upload & process</button>
        {message ? <span className="text-sm text-emerald-700">{message}</span> : null}
        <div className="w-full"><ErrorBox error={uploadError} /></div>
      </form>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty>No documents yet.</Empty> : (
        <Card>
          <table className="table">
            <thead><tr><th>Document</th><th>Class</th><th>Status</th><th>Parsed</th><th>Processing</th><th>Uploaded</th></tr></thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.id}>
                  <td>{d.title ?? d.filename}<div className="text-xs text-ink-muted">{d.filename} {d.is_synthetic ? <Badge value="SYNTHETIC" tone="amber" /> : null}</div></td>
                  <td><Badge value={d.doc_class} tone="blue" /></td>
                  <td><Badge value={d.status} tone={d.status === "PROCESSED" ? "green" : d.status === "FAILED" ? "red" : "gray"} /></td>
                  <td className="text-xs">{d.parse_report?.paragraphs ?? "—"} paras · {d.parse_report?.chunks ?? "—"} chunks{d.parse_report?.quarantined_chunks ? <Badge value={`${d.parse_report.quarantined_chunks} QUARANTINED`} tone="red" /> : null}</td>
                  <td>{d.run_id ? <A href={`/runs/${d.run_id}`}><Badge value={d.run_status} /></A> : "—"}</td>
                  <td className="text-xs text-ink-muted">{fmtDateTime(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
